import os
import shutil
import textwrap
from datetime import datetime
from functools import partial
from io import StringIO
from unittest.mock import patch

import pytest
from pkgdev.mangle import copyright_regex, keywords_regex
from pkgdev.scripts import run
from snakeoil.contexts import chdir, os_environ
from snakeoil.osutils import pjoin


class TestPkgdevCommitParseArgs:

    def test_non_repo_cwd(self, capsys, tool):
        with pytest.raises(SystemExit) as excinfo:
            tool.parse_args(['commit'])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        assert err.strip() == 'pkgdev commit: error: not in ebuild repo'

    def test_bad_repo_cwd(self, make_repo, capsys, tool):
        repo = make_repo(masters=('nonexistent',))
        with pytest.raises(SystemExit) as excinfo, \
                chdir(repo.location):
            tool.parse_args(['commit'])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        assert err.strip().startswith('pkgdev commit: error: repo init failed')

    def test_non_git_repo_cwd(self, repo, capsys, tool):
        with pytest.raises(SystemExit) as excinfo, \
                chdir(repo.location):
            tool.parse_args(['commit'])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        assert err.strip() == 'pkgdev commit: error: not in git repo'

    def test_non_ebuild_git_repo_cwd(self, make_repo, git_repo, capsys, tool):
        os.mkdir(pjoin(git_repo.path, 'repo'))
        repo = make_repo(pjoin(git_repo.path, 'repo'))
        with pytest.raises(SystemExit) as excinfo, \
                chdir(repo.location):
            tool.parse_args(['commit'])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        assert err.strip() == 'pkgdev commit: error: not in ebuild git repo'

    def test_commit_signing(self, repo, make_git_repo, tool):
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0', commit=False)
        # signed commits aren't enabled by default
        with chdir(repo.location):
            options, _ = tool.parse_args(['commit', '-u'])
            assert '--signoff' not in options.commit_args
            assert '--gpg-sign' not in options.commit_args

            options, _ = tool.parse_args(['commit', '-u', '--signoff'])
            assert '--signoff' in options.commit_args
            assert '--gpg-sign' not in options.commit_args
        # signed commits enabled by layout.conf setting
        with open(pjoin(git_repo.path, 'metadata/layout.conf'), 'a+') as f:
            f.write('sign-commits = true\n')
        with chdir(repo.location):
            options, _ = tool.parse_args(['commit', '-u'])
            assert '--signoff' not in options.commit_args
            assert '--gpg-sign' in options.commit_args

            options, _ = tool.parse_args(['commit', '-u', '--signoff'])
            assert '--signoff' in options.commit_args
            assert '--gpg-sign' in options.commit_args

    def test_git_commit_args(self, repo, make_git_repo, tool):
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0', commit=False)
        with chdir(repo.location):
            for opt, expected in (
                    ('-n', '--dry-run'),
                    ('--dry-run', '--dry-run'),
                    ('-v', '-v'),
                    ('--verbose', '-v'),
                    ):
                options, _ = tool.parse_args(['commit', opt])
                assert expected in options.commit_args

    def test_git_commit_args_passthrough(self, repo, make_git_repo, tool):
        """Unknown arguments for ``pkgdev commit`` are passed to ``git commit``."""
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0', commit=False)
        with chdir(repo.location):
            for opt in ('--author="A U Thor <author@example.com>"', '-e'):
                options, _ = tool.parse_args(['commit', opt])
            assert options.commit_args == [opt]

    def test_scan_args(self, repo, make_git_repo, tool):
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0', commit=False)
        # pkgcheck isn't run in verbose mode by default
        with chdir(repo.location):
            options, _ = tool.parse_args(['commit'])
        assert '-v' not in options.scan_args
        # verbosity level is passed down to pkgcheck
        with chdir(repo.location):
            options, _ = tool.parse_args(['commit', '-v'])
        assert '-v' in options.scan_args

    def test_commit_tags(self, capsys, repo, make_git_repo, tool):
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0', commit=False)
        with chdir(repo.location):
            # bug IDs
            for opt in ('-b', '--bug'):
                options, _ = tool.parse_args(['commit', opt, '1'])
                assert options.footer == {('Bug', 'https://bugs.gentoo.org/1')}

            # bug URLs
            for opt in ('-b', '--bug'):
                options, _ = tool.parse_args(['commit', opt, 'https://bugs.gentoo.org/2'])
                assert options.footer == {('Bug', 'https://bugs.gentoo.org/2')}

            # bug IDs
            for opt in ('-c', '--closes'):
                options, _ = tool.parse_args(['commit', opt, '1'])
                assert options.footer == {('Closes', 'https://bugs.gentoo.org/1')}

            # bug URLs
            for opt in ('-c', '--closes'):
                options, _ = tool.parse_args(['commit', opt, 'https://bugs.gentoo.org/2'])
                assert options.footer == {('Closes', 'https://bugs.gentoo.org/2')}

            # bad URL
            for opt in ('-b', '-c'):
                with pytest.raises(SystemExit) as excinfo:
                    tool.parse_args(['commit', opt, 'bugs.gentoo.org/1'])
                assert excinfo.value.code == 2
                out, err = capsys.readouterr()
                assert not out
                assert 'invalid URL: bugs.gentoo.org/1' in err

            # generic tags
            for opt in ('-T', '--tag'):
                for value, expected in (
                        ('tag:value', ('Tag', 'value')),
                        ('tag:multiple values', ('Tag', 'multiple values')),
                        ('tag:multiple:values', ('Tag', 'multiple:values')),
                        ):
                    options, _ = tool.parse_args(['commit', opt, value])
                    assert options.footer == {expected}

            # bad tags
            for opt in ('-T', '--tag'):
                for value in ('', ':', 'tag:', ':value', 'tag'):
                    with pytest.raises(SystemExit) as excinfo:
                        tool.parse_args(['commit', opt, value])
                    assert excinfo.value.code == 2
                    out, err = capsys.readouterr()
                    assert not out
                    assert 'invalid commit tag' in err


class TestPkgdevCommit:

    script = staticmethod(partial(run, 'pkgdev'))

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.cache_dir = str(tmp_path)
        self.scan_args = ['--pkgcheck-scan', f'--config no --cache-dir {self.cache_dir}']
        # args for running pkgdev like a script
        self.args = ['pkgdev', 'commit', '--config', 'no'] + self.scan_args

    def test_empty_repo(self, capsys, repo, make_git_repo):
        git_repo = make_git_repo(repo.location, commit=True)
        with patch('sys.argv', self.args), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(git_repo.path):
            self.script()
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        assert not out
        assert err.strip() == 'pkgdev commit: error: no staged changes exist'

    def test_git_message_opts(self, repo, make_git_repo, tmp_path):
        """Verify message-related options are passed through to `git commit`."""
        git_repo = make_git_repo(repo.location, commit=True)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0', commit=False)
        path = str(tmp_path / 'msg')
        with open(path, 'w') as f:
            f.write('commit1')

        with patch('sys.argv', self.args + ['-u', '-F', path]), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(git_repo.path):
            self.script()
        assert excinfo.value.code == 0
        commit_msg = git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])
        assert commit_msg == ['commit1']

        repo.create_ebuild('cat/pkg-1')
        git_repo.add_all('cat/pkg-1', commit=False)
        with os_environ(GIT_EDITOR="sed -i '1s/1/2/'"), \
                patch('sys.argv', self.args + ['-u', '-t', path]), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(git_repo.path):
            self.script()
        assert excinfo.value.code == 0
        commit_msg = git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])
        assert commit_msg == ['commit2']

    def test_message_template(self, capsys, repo, make_git_repo, tmp_path):
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')
        path = str(tmp_path / 'msg')

        # auto-generate prefix
        with open(path, 'w') as f:
            f.write(textwrap.dedent("""\
                *: summary

                body
            """))

        for i, opt in enumerate(['-M', '--message-template'], 1):
            repo.create_ebuild(f'cat/pkg-{i}')
            git_repo.add_all(f'cat/pkg-{i}', commit=False)
            with patch('sys.argv', self.args + ['-u', opt, path]), \
                    pytest.raises(SystemExit) as excinfo, \
                    chdir(git_repo.path):
                self.script()
            assert excinfo.value.code == 0
            commit_msg = git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])
            assert commit_msg == ['cat/pkg: summary', '', 'body']

        # override prefix
        with open(path, 'w') as f:
            f.write(textwrap.dedent("""\
                prefix: summary

                body
            """))

        for i, opt in enumerate(['-M', '--message-template'], 3):
            repo.create_ebuild(f'cat/pkg-{i}')
            git_repo.add_all(f'cat/pkg-{i}', commit=False)
            with patch('sys.argv', self.args + ['-u', opt, path]), \
                    pytest.raises(SystemExit) as excinfo, \
                    chdir(git_repo.path):
                self.script()
            assert excinfo.value.code == 0
            commit_msg = git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])
            assert commit_msg == ['prefix: summary', '', 'body']

        # empty message
        with open(path, 'w') as f:
            f.write('')

        for i, opt in enumerate(['-M', '--message-template'], 5):
            repo.create_ebuild(f'cat/pkg-{i}')
            git_repo.add_all(f'cat/pkg-{i}', commit=False)
            with patch('sys.argv', self.args + ['-u', opt, path]), \
                    pytest.raises(SystemExit) as excinfo, \
                    chdir(git_repo.path):
                self.script()
            assert excinfo.value.code == 2
            out, err = capsys.readouterr()
            assert not out
            assert err.strip().startswith('pkgdev commit: error: empty message template')

    def test_custom_unprefixed_message(self, capsys, repo, make_git_repo):
        git_repo = make_git_repo(repo.location)
        ebuild_path = repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')
        with open(ebuild_path, 'a+') as f:
            f.write('# comment\n')

        with patch('sys.argv', self.args + ['-u', '-m', 'msg']), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(git_repo.path):
            self.script()
        assert excinfo.value.code == 0
        out, err = capsys.readouterr()
        assert err == out == ''

        commit_msg = git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])
        assert commit_msg == ['cat/pkg: msg']

    def test_custom_prefixed_message(self, capsys, repo, make_git_repo):
        git_repo = make_git_repo(repo.location)
        ebuild_path = repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')
        with open(ebuild_path, 'a+') as f:
            f.write('# comment\n')

        with patch('sys.argv', self.args + ['-u', '-m', 'prefix: msg']), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(git_repo.path):
            self.script()
        assert excinfo.value.code == 0
        out, err = capsys.readouterr()
        assert err == out == ''

        commit_msg = git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])
        assert commit_msg == ['prefix: msg']

    def test_edited_commit_message(self, capsys, repo, make_git_repo):
        git_repo = make_git_repo(repo.location)
        ebuild_path = repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')
        with open(ebuild_path, 'a+') as f:
            f.write('# comment\n')

        with os_environ(GIT_EDITOR="sed -i '1s/$/commit/'"), \
                patch('sys.argv', self.args + ['-u']), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(git_repo.path):
            self.script()
        assert excinfo.value.code == 0
        out, err = capsys.readouterr()
        assert err == out == ''

        commit_msg = git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])
        assert commit_msg == ['cat/pkg: commit']

    def test_generated_commit_prefixes(self, capsys, repo, make_git_repo):
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')

        def commit():
            with patch('sys.argv', self.args + ['-a', '-m', 'msg']), \
                    pytest.raises(SystemExit) as excinfo, \
                    chdir(git_repo.path):
                self.script()
            assert excinfo.value.code == 0
            out, err = capsys.readouterr()
            assert err == out == ''
            message = git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])
            return message[0]

        # single package change
        repo.create_ebuild('cat/newpkg-0')
        assert commit().startswith('cat/newpkg: ')

        # multiple package changes in the same category
        repo.create_ebuild('cat/newpkg-1')
        repo.create_ebuild('cat/pkg-1')
        assert commit().startswith('cat/*: ')

        # multiple package changes in various categories
        repo.create_ebuild('cat/newpkg-2')
        repo.create_ebuild('cat/pkg-2')
        repo.create_ebuild('newcat/newpkg-1')
        assert commit().startswith('*/*: ')

        # single eclass change
        with open(pjoin(repo.location, 'eclass', 'foo.eclass'), 'a+') as f:
            f.write('# comment\n')
        assert commit().startswith('foo.eclass: ')

        # multiple eclass changes
        for eclass in ('foo.eclass', 'bar.eclass'):
            with open(pjoin(repo.location, 'eclass', eclass), 'a+') as f:
                f.write('# comment\n')
        assert commit().startswith('eclass: ')

        # single profiles/package.mask change
        with open(pjoin(repo.location, 'profiles', 'package.mask'), 'a+') as f:
            f.write('# comment\n')
        assert commit().startswith('profiles: ')

        amd64 = pjoin(repo.location, 'profiles', 'arch', 'amd64')
        os.makedirs(amd64)
        arm64 = pjoin(repo.location, 'profiles', 'arch', 'arm64')
        os.makedirs(arm64)

        # multiple profiles file changes in the same subdir
        for file in ('package.mask', 'package.mask'):
            with open(pjoin(amd64, file), 'a+') as f:
                f.write('# comment\n')
        assert commit().startswith('profiles/arch/amd64: ')

        # multiple profiles file changes in different subdirs
        for path in (amd64, arm64):
            with open(pjoin(path, 'package.use'), 'a+') as f:
                f.write('# comment\n')
        assert commit().startswith('profiles/arch: ')

        # single repo root file change
        with open(pjoin(repo.location, 'skel.ebuild'), 'a+') as f:
            f.write('# comment\n')
        assert commit().startswith('skel.ebuild: ')

        # multiple repo root file change (no commit message prefix)
        for file in ('skel.ebuild', 'header.txt'):
            with open(pjoin(repo.location, file), 'a+') as f:
                f.write('# comment\n')
        assert commit() == 'msg'

        # treewide changes (no commit message prefix)
        repo.create_ebuild('foo/bar-1')
        with open(pjoin(repo.location, 'eclass', 'foo.eclass'), 'a+') as f:
            f.write('# comment\n')
        with open(pjoin(repo.location, 'profiles', 'package.mask'), 'a+') as f:
            f.write('# comment\n')
        assert commit() == 'msg'

    def test_generated_commit_summaries(self, capsys, repo, make_git_repo):
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')

        def commit():
            with os_environ(GIT_EDITOR="sed -i '1s/$/summary/'"), \
                    patch('sys.argv', self.args + ['-a']), \
                    pytest.raises(SystemExit) as excinfo, \
                    chdir(git_repo.path):
                self.script()
            assert excinfo.value.code == 0
            out, err = capsys.readouterr()
            assert err == out == ''
            message = git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])
            return message[0]

        # initial package import
        repo.create_ebuild('cat/newpkg-0')
        assert commit() == 'cat/newpkg: new package, add 0'

        # initial package import, overflowed title truncated
        for i in range(10):
            repo.create_ebuild(f'cat/newpkg2-{i}.0.0')
        assert commit() == 'cat/newpkg2: new package'

        # single addition
        repo.create_ebuild('cat/pkg-1')
        assert commit() == 'cat/pkg: add 1'

        # multiple additions
        repo.create_ebuild('cat/pkg-2')
        repo.create_ebuild('cat/pkg-3')
        repo.create_ebuild('cat/pkg-4', eapi=6)
        assert commit() == 'cat/pkg: add 2, 3, 4'

        # revbump updating EAPI
        repo.create_ebuild('cat/pkg-4-r1', eapi=7)
        assert commit() == 'cat/pkg: update EAPI 6 -> 7'

        # single rename with no revisions
        git_repo.move(
            pjoin(git_repo.path, 'cat/pkg/pkg-4.ebuild'),
            pjoin(git_repo.path, 'cat/pkg/pkg-5.ebuild'),
            commit=False
        )
        assert commit() == 'cat/pkg: add 5, drop 4'

        # bump EAPI
        repo.create_ebuild('cat/pkg-6', eapi='6')
        git_repo.add_all('cat/pkg-6')
        repo.create_ebuild('cat/pkg-6', eapi='7')
        assert commit() == 'cat/pkg: update EAPI 6 -> 7'

        # update description
        repo.create_ebuild('cat/pkg-7')
        git_repo.add_all('cat/pkg-7')
        repo.create_ebuild('cat/pkg-7', description='something')
        assert commit() == 'cat/pkg: update DESCRIPTION'

        # update description & homepage
        repo.create_ebuild('cat/pkg-7', description='another something', homepage='https://gentoo.org')
        assert commit() == 'cat/pkg: update DESCRIPTION, HOMEPAGE'

        # update string_targets (USE_RUBY)
        repo.create_ebuild('cat/pkg-8', use_ruby='ruby27')
        git_repo.add_all('cat/pkg-8')
        repo.create_ebuild('cat/pkg-8', use_ruby='ruby27 ruby30')
        assert commit() == 'cat/pkg: enable ruby30'
        repo.create_ebuild('cat/pkg-8', use_ruby='ruby30')
        assert commit() == 'cat/pkg: disable ruby27'
        repo.create_ebuild('cat/pkg-8', use_ruby=' '.join(f'ruby{i}' for i in range(30, 40)))
        assert commit() == 'cat/pkg: update USE_RUBY support'

        # update array_targets (PYTHON_COMPAT)
        repo.create_ebuild('cat/pkg-9', data='PYTHON_COMPAT=( python3_9 )')
        git_repo.add_all('cat/pkg-9')
        repo.create_ebuild('cat/pkg-9', data='PYTHON_COMPAT=( python3_{9..10} )')
        assert commit() == 'cat/pkg: enable py3.10'
        repo.create_ebuild('cat/pkg-9', data='PYTHON_COMPAT=( python3_10 )')
        assert commit() == 'cat/pkg: disable py3.9'


        # multiple ebuild modifications don't get a generated summary
        repo.create_ebuild('cat/pkg-5', keywords=['~amd64'])
        repo.create_ebuild('cat/pkg-6', keywords=['~amd64'])
        assert commit() == 'cat/pkg: summary'

        # large number of additions in a single commit
        for v in range(10000, 10010):
            repo.create_ebuild(f'cat/pkg-{v}')
        assert commit() == 'cat/pkg: add versions'

        # create Manifest
        with open(pjoin(git_repo.path, 'cat/pkg/Manifest'), 'w') as f:
            f.write('DIST pkg-3.tar.gz 101 BLAKE2B deadbeef SHA512 deadbeef\n')
        assert commit() == 'cat/pkg: update Manifest'
        # update Manifest
        with open(pjoin(git_repo.path, 'cat/pkg/Manifest'), 'a+') as f:
            f.write('DIST pkg-2.tar.gz 101 BLAKE2B deadbeef SHA512 deadbeef\n')
        assert commit() == 'cat/pkg: update Manifest'
        # remove Manifest
        os.remove(pjoin(git_repo.path, 'cat/pkg/Manifest'))
        assert commit() == 'cat/pkg: update Manifest'

        # single removals
        os.remove(pjoin(git_repo.path, 'cat/pkg/pkg-4-r1.ebuild'))
        assert commit() == 'cat/pkg: drop 4-r1'
        os.remove(pjoin(git_repo.path, 'cat/pkg/pkg-3.ebuild'))
        assert commit() == 'cat/pkg: drop 3'

        # multiple removal
        os.remove(pjoin(git_repo.path, 'cat/pkg/pkg-2.ebuild'))
        os.remove(pjoin(git_repo.path, 'cat/pkg/pkg-1.ebuild'))
        assert commit() == 'cat/pkg: drop 1, 2'

        # large number of removals in a single commit
        for v in range(10000, 10010):
            os.remove(pjoin(git_repo.path, f'cat/pkg/pkg-{v}.ebuild'))
        assert commit() == 'cat/pkg: drop versions'

        # package rename
        shutil.copytree(pjoin(git_repo.path, 'cat/pkg'), pjoin(git_repo.path, 'newcat/pkg'))
        shutil.rmtree(pjoin(git_repo.path, 'cat/pkg'))
        assert commit() == 'newcat/pkg: rename cat/pkg'

        # treeclean
        shutil.rmtree(pjoin(git_repo.path, 'newcat/pkg'))
        assert commit() == 'newcat/pkg: treeclean'

    def test_generated_commit_summaries_keywords(self, capsys, make_repo, make_git_repo):
        repo = make_repo(arches=['amd64', 'arm64', 'ia64', 'x86'])
        git_repo = make_git_repo(repo.location)
        pkgdir = os.path.dirname(repo.create_ebuild('cat/pkg-0'))
        with open(pjoin(pkgdir, 'metadata.xml'), 'w') as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE pkgmetadata SYSTEM "http://www.gentoo.org/dtd/metadata.dtd">
                <pkgmetadata>
                    <stabilize-allarches/>
                </pkgmetadata>
            """))
        git_repo.add_all('cat/pkg-0')

        def commit():
            with os_environ(GIT_EDITOR="sed -i '1s/$/summary/'"), \
                    patch('sys.argv', self.args + ['-a']), \
                    pytest.raises(SystemExit) as excinfo, \
                    chdir(git_repo.path):
                self.script()
            assert excinfo.value.code == 0
            out, err = capsys.readouterr()
            assert err == out == ''
            message = git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])
            return message[0]

        # keyword version
        repo.create_ebuild('cat/pkg-0', keywords=['~amd64'])
        assert commit() == 'cat/pkg: keyword 0 for ~amd64'

        # stabilize version
        repo.create_ebuild('cat/pkg-0', keywords=['amd64'])
        assert commit() == 'cat/pkg: stabilize 0 for amd64'

        # destabilize version
        repo.create_ebuild('cat/pkg-0', keywords=['~amd64'])
        assert commit() == 'cat/pkg: destabilize 0 for ~amd64'

        # unkeyword version
        repo.create_ebuild('cat/pkg-0')
        assert commit() == 'cat/pkg: unkeyword 0 for ~amd64'

        with open(pjoin(repo.location, 'profiles', 'arches.desc'), 'w') as f:
            f.write(textwrap.dedent("""\
                amd64 stable
                arm64 stable
                ia64  testing
                x86   stable
            """))
        git_repo.add_all('set arches.desc')

        repo.create_ebuild('cat/pkg-0', keywords=['~amd64', '~arm64', '~ia64', '~x86'])
        git_repo.add_all('cat/pkg-0')

        # stabilize version
        repo.create_ebuild('cat/pkg-0', keywords=['amd64', '~arm64', '~ia64', '~x86'])
        assert commit() == 'cat/pkg: stabilize 0 for amd64'

        # stabilize version ALLARCHES
        repo.create_ebuild('cat/pkg-0', keywords=['amd64', 'arm64', '~ia64', 'x86'])
        assert commit() == 'cat/pkg: stabilize 0 for ALLARCHES'

        # stabilize version
        repo.create_ebuild('cat/newpkg-0', keywords=['~amd64', '~arm64', '~ia64', '~x86'])
        git_repo.add_all('cat/newpkg')
        repo.create_ebuild('cat/newpkg-0', keywords=['amd64', 'arm64', '~ia64', 'x86'])
        assert commit() == 'cat/newpkg: stabilize 0 for amd64, arm64, x86'

    def test_metadata_summaries(self, capsys, repo, make_git_repo):
        git_repo = make_git_repo(repo.location)
        pkgdir = os.path.dirname(repo.create_ebuild('cat/pkg-0'))
        # stub metadata
        with open(pjoin(pkgdir, 'metadata.xml'), 'w') as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE pkgmetadata SYSTEM "http://www.gentoo.org/dtd/metadata.dtd">
                <pkgmetadata>
                    <maintainer type="person">
                        <email>person@email.com</email>
                        <name>Person</name>
                    </maintainer>
                </pkgmetadata>
            """))
        git_repo.add_all('cat/pkg-0')

        def commit():
            with os_environ(GIT_EDITOR="sed -i '1s/$/summary/'"), \
                    patch('sys.argv', self.args + ['-a']), \
                    pytest.raises(SystemExit) as excinfo, \
                    chdir(git_repo.path):
                self.script()
            assert excinfo.value.code == 0
            out, err = capsys.readouterr()
            assert err == out == ''
            message = git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])
            return message[0]

        # add yourself
        with open(pjoin(pkgdir, 'metadata.xml'), 'w') as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE pkgmetadata SYSTEM "http://www.gentoo.org/dtd/metadata.dtd">
                <pkgmetadata>
                    <maintainer type="person">
                        <email>person@email.com</email>
                        <name>Person</name>
                    </maintainer>
                    <maintainer type="person">
                        <email>first.last@email.com</email>
                        <name>First Last</name>
                    </maintainer>
                </pkgmetadata>
            """))
        assert commit() == 'cat/pkg: add myself as a maintainer'

        # drop yourself
        with open(pjoin(pkgdir, 'metadata.xml'), 'w') as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE pkgmetadata SYSTEM "http://www.gentoo.org/dtd/metadata.dtd">
                <pkgmetadata>
                    <maintainer type="person">
                        <email>person@email.com</email>
                        <name>Person</name>
                    </maintainer>
                </pkgmetadata>
            """))
        assert commit() == 'cat/pkg: drop myself as a maintainer'

        # drop to maintainer-needed
        with open(pjoin(pkgdir, 'metadata.xml'), 'w') as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE pkgmetadata SYSTEM "http://www.gentoo.org/dtd/metadata.dtd">
                <pkgmetadata>
                </pkgmetadata>
            """))
        assert commit() == 'cat/pkg: drop to maintainer-needed'

        # add random maintainer
        with open(pjoin(pkgdir, 'metadata.xml'), 'w') as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE pkgmetadata SYSTEM "http://www.gentoo.org/dtd/metadata.dtd">
                <pkgmetadata>
                    <maintainer type="person">
                        <email>person@email.com</email>
                        <name>Person</name>
                    </maintainer>
                </pkgmetadata>
            """))
        assert commit() == 'cat/pkg: update maintainers'

        # add allarches tag
        with open(pjoin(pkgdir, 'metadata.xml'), 'w') as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE pkgmetadata SYSTEM "http://www.gentoo.org/dtd/metadata.dtd">
                <pkgmetadata>
                    <maintainer type="person">
                        <email>person@email.com</email>
                        <name>Person</name>
                    </maintainer>
                    <stabilize-allarches/>
                </pkgmetadata>
            """))
        assert commit() == 'cat/pkg: mark ALLARCHES'

        # drop allarches tag
        with open(pjoin(pkgdir, 'metadata.xml'), 'w') as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE pkgmetadata SYSTEM "http://www.gentoo.org/dtd/metadata.dtd">
                <pkgmetadata>
                    <maintainer type="person">
                        <email>person@email.com</email>
                        <name>Person</name>
                    </maintainer>
                </pkgmetadata>
            """))
        assert commit() == 'cat/pkg: drop ALLARCHES'

        # add upstream metadata
        with open(pjoin(pkgdir, 'metadata.xml'), 'w') as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE pkgmetadata SYSTEM "http://www.gentoo.org/dtd/metadata.dtd">
                <pkgmetadata>
                    <maintainer type="person">
                        <email>person@email.com</email>
                        <name>Person</name>
                    </maintainer>
                    <upstream>
                        <remote-id type="github">pkgcore/pkgdev</remote-id>
                        <remote-id type="pypi">pkgdev</remote-id>
                    </upstream>
                </pkgmetadata>
            """))
        assert commit() == 'cat/pkg: add github, pypi upstream metadata'

        # remove upstream metadata
        with open(pjoin(pkgdir, 'metadata.xml'), 'w') as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE pkgmetadata SYSTEM "http://www.gentoo.org/dtd/metadata.dtd">
                <pkgmetadata>
                    <maintainer type="person">
                        <email>person@email.com</email>
                        <name>Person</name>
                    </maintainer>
                    <upstream>
                        <remote-id type="github">pkgcore/pkgdev</remote-id>
                    </upstream>
                </pkgmetadata>
            """))
        assert commit() == 'cat/pkg: remove pypi upstream metadata'

        # update upstream metadata
        with open(pjoin(pkgdir, 'metadata.xml'), 'w') as f:
            f.write(textwrap.dedent("""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE pkgmetadata SYSTEM "http://www.gentoo.org/dtd/metadata.dtd">
                <pkgmetadata>
                    <maintainer type="person">
                        <email>person@email.com</email>
                        <name>Person</name>
                    </maintainer>
                    <upstream>
                        <remote-id type="github">pkgcore/pkgcheck</remote-id>
                    </upstream>
                </pkgmetadata>
            """))
        assert commit() == 'cat/pkg: update upstream metadata'

    def test_no_summary(self, capsys, repo, make_git_repo):
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')

        def commit(args):
            with os_environ(GIT_EDITOR="sed -i '1s/$/summary/'"), \
                    patch('sys.argv', self.args + args), \
                    pytest.raises(SystemExit) as excinfo, \
                    chdir(git_repo.path):
                self.script()
            assert excinfo.value.code == 0
            out, err = capsys.readouterr()
            assert err == out == ''
            return git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])

        # no commit message content
        for i in range(10):
            with open(pjoin(git_repo.path, f'file-a-{i}'), 'w') as f:
                f.write('stub\n')
        assert commit(['-a']) == ['summary']

        # footer exists with no generated summary
        for i in range(10):
            with open(pjoin(git_repo.path, f'file-b-{i}'), 'w') as f:
                f.write('stub\n')
        assert commit(['-a', '-T', 'tag:value']) == ['summary', '', 'Tag: value']

    def test_non_gentoo_file_mangling(self, repo, make_git_repo):
        git_repo = make_git_repo(repo.location)
        ebuild_path = repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')

        def commit(args):
            with patch('sys.argv', self.args + args), \
                    pytest.raises(SystemExit) as excinfo, \
                    chdir(git_repo.path):
                self.script()
            assert excinfo.value.code == 0

        # append line missing EOF newline to ebuild
        with open(ebuild_path, 'a+') as f:
            f.write('# comment')
        # verify file doesn't end with newline
        with open(ebuild_path) as f:
            assert f.read()[-1] != '\n'

        # non-gentoo repos aren't mangled by default
        commit(['-u', '-m', 'mangling'])
        with open(ebuild_path) as f:
            assert f.read()[-1] != '\n'

        # but they can be forcibly mangled
        with open(ebuild_path, 'a+') as f:
            f.write('# comment')
        commit(['--mangle', '-u', '-m', 'mangling'])
        # mangled pre-commit, file now ends with newline
        with open(ebuild_path) as f:
            assert f.read()[-1] == '\n'

        # FILESDIR content is ignored even when forced
        path = pjoin(os.path.dirname(ebuild_path), 'files', 'pkg.patch')
        os.makedirs(os.path.dirname(path))
        with open(path, 'w') as f:
            f.write('# comment')
        # verify file doesn't end with newline
        with open(path) as f:
            assert f.read()[-1] != '\n'

    def test_gentoo_file_mangling(self, make_repo, make_git_repo):
        repo = make_repo(repo_id='gentoo')
        git_repo = make_git_repo(repo.location)
        ebuild_path = repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')

        def commit(args):
            with patch('sys.argv', self.args + args), \
                    pytest.raises(SystemExit) as excinfo, \
                    chdir(git_repo.path):
                self.script()
            assert excinfo.value.code == 0

        # append line missing EOF newline to ebuild
        with open(ebuild_path, 'a+') as f:
            f.write('# comment')
        # verify file doesn't end with newline
        with open(ebuild_path) as f:
            assert f.read()[-1] != '\n'

        # gentoo repos are mangled by default
        commit(['-n', '-u', '-m', 'mangling'])
        with open(ebuild_path) as f:
            assert f.read()[-1] == '\n'

        # FILESDIR content is ignored
        path = pjoin(os.path.dirname(ebuild_path), 'files', 'pkg.patch')
        os.makedirs(os.path.dirname(path))
        with open(path, 'w') as f:
            f.write('# comment')
        # verify file doesn't end with newline
        with open(path) as f:
            assert f.read()[-1] != '\n'

        for years, org in (
                ('1999-2020', 'Gentoo Authors'),
                ('1999-2020', 'Gentoo Foundation'),
                ('2020', 'Gentoo Authors'),
                ('2020', 'Gentoo Foundation'),
                ):
            # munge the copyright header
            with open(ebuild_path, 'r+') as f:
                lines = f.read().splitlines()
                lines[0] = f'# Copyright {years} {org}\n'
                f.seek(0)
                f.truncate()
                f.write('\n'.join(lines) + '\n')
            commit(['-n', '-u', '-m', 'mangling'])
            # verify the copyright header was updated
            with open(ebuild_path) as f:
                lines = f.read().splitlines()
                mo = copyright_regex.match(lines[0])
                assert mo.group('end') == str(datetime.today().year)
                assert mo.group('begin') == years[:4] + '-'
                assert mo.group('holder') == 'Gentoo Authors'

        for original, expected in (
                ('"arm64 amd64 x86"', 'amd64 arm64 x86'),
                ('"arm64 amd64 ~x86"', 'amd64 arm64 ~x86'),
                ('"arm64 ~x86 amd64"', 'amd64 arm64 ~x86'),
                ('"arm64 ~x86 ~amd64"', '~amd64 arm64 ~x86'),
                ('arm64 ~x86 ~amd64', '~amd64 arm64 ~x86'),
                ):
            # munge the keywords
            with open(ebuild_path, 'r+') as f:
                lines = f.read().splitlines()
                lines[-1] = f'KEYWORDS={original}'
                f.seek(0)
                f.truncate()
                f.write('\n'.join(lines) + '\n')
            commit(['-n', '-u', '-m', 'mangling'])
            # verify the keywords were updated
            with open(ebuild_path) as f:
                lines = f.read().splitlines()
                mo = keywords_regex.match(lines[-1])
                assert mo.group('keywords') == expected

    def test_scan(self, capsys, repo, make_git_repo):
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')

        for i, opt in enumerate(['-s', '--scan'], 1):
            repo.create_ebuild(f'cat/pkg-{i}')
            git_repo.add_all(f'cat/pkg-{i}', commit=False)
            with patch('sys.argv', self.args + [opt]), \
                    pytest.raises(SystemExit) as excinfo, \
                    chdir(git_repo.path):
                self.script()
            assert excinfo.value.code == 0
            out, err = capsys.readouterr()
            assert err == out == ''
            commit_msg = git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])
            assert commit_msg == [f'cat/pkg: add {i}']

    def test_failed_scan(self, capsys, repo, make_git_repo):
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')

        # verify staged changes via `pkgcheck scan` before creating commit
        repo.create_ebuild('cat/pkg-1', license='')
        git_repo.add_all('cat/pkg-1', commit=False)
        with patch('sys.argv', self.args + ['--scan']), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(git_repo.path):
            self.script()
        assert excinfo.value.code == 1
        out, err = capsys.readouterr()
        assert not err
        assert out == textwrap.dedent("""\
            cat/pkg
              MissingLicense: version 1: no license defined

            FAILURES
            cat/pkg
              MissingLicense: version 1: no license defined
        """)

        # ignore failures to create the commit
        with patch('sys.argv', self.args + ['--scan', '--ask']), \
                patch('sys.stdin', StringIO('y\n')), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(git_repo.path):
            self.script()
        assert excinfo.value.code == 0

    def test_config_opts(self, capsys, repo, make_git_repo, tmp_path):
        config_file = str(tmp_path / 'config')
        with open(config_file, 'w') as f:
            f.write(textwrap.dedent("""
                [DEFAULT]
                commit.scan=
            """))

        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')
        repo.create_ebuild('cat/pkg-1', license='')
        git_repo.add_all('cat/pkg-1', commit=False)
        with patch('sys.argv', ['pkgdev', 'commit', '--config', config_file] + self.scan_args), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(git_repo.path):
            self.script()
        out, err = capsys.readouterr()
        assert excinfo.value.code == 1
        assert not err
        assert 'MissingLicense' in out

    def test_config_repo_opts(self, capsys, repo, make_git_repo, tmp_path):
        config_file = str(tmp_path / 'config')
        with open(config_file, 'w') as f:
            f.write(textwrap.dedent("""
                [fake]
                commit.scan=
            """))

        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')
        repo.create_ebuild('cat/pkg-1', license='')
        git_repo.add_all('cat/pkg-1', commit=False)
        with patch('sys.argv', ['pkgdev', 'commit', '--config', config_file] + self.scan_args), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(git_repo.path):
            self.script()
        out, err = capsys.readouterr()
        assert excinfo.value.code == 1
        assert not err
        assert 'MissingLicense' in out

    def test_failed_manifest(self, capsys, repo, make_git_repo):
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')
        repo.create_ebuild('cat/pkg-1', eapi='-1')
        git_repo.add_all('cat/pkg-1', commit=False)
        with patch('sys.argv', self.args), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(git_repo.path):
            self.script()
        assert excinfo.value.code == 1
        out, err = capsys.readouterr()
        assert not err
        assert out == " * cat/pkg-1: invalid EAPI '-1'\n"
