import os
import shutil
import textwrap
from datetime import datetime
from functools import partial
from io import StringIO
from unittest.mock import patch

import pytest
from pkgdev.mangle import copyright_regex
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
        # signed commits enabled by layout.conf setting
        with open(pjoin(git_repo.path, 'metadata/layout.conf'), 'a+') as f:
            f.write('sign-commits = true\n')
        with chdir(repo.location):
            options, _ = tool.parse_args(['commit', '-u'])
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

    script = partial(run, 'pkgdev')

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.cache_dir = str(tmp_path)
        self.scan_args = ['--pkgcheck-scan', f'--config no --cache-dir {self.cache_dir}']
        # args for running pkgdev like a script
        self.args = ['pkgdev', 'commit'] + self.scan_args

    def test_empty_repo(self, capsys, repo, make_git_repo):
        git_repo = make_git_repo(repo.location)
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
        git_repo = make_git_repo(repo.location)
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
        assert commit() == 'cat/newpkg: initial import'

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

        # keyword version
        repo.create_ebuild('cat/pkg-6')
        git_repo.add_all('cat/pkg-6')
        repo.create_ebuild('cat/pkg-6', keywords=['~amd64'])
        assert commit() == 'cat/pkg: keyword 6 for ~amd64'

        # stabilize version
        repo.create_ebuild('cat/pkg-6', keywords=['amd64'])
        assert commit() == 'cat/pkg: stabilize 6 for amd64'

        # destabilize version
        repo.create_ebuild('cat/pkg-6', keywords=['~amd64'])
        assert commit() == 'cat/pkg: destabilize 6 for ~amd64'

        # unkeyword version
        repo.create_ebuild('cat/pkg-6', eapi='6')
        assert commit() == 'cat/pkg: unkeyword 6 for ~amd64'

        # bump EAPI
        repo.create_ebuild('cat/pkg-6', eapi='7')
        assert commit() == 'cat/pkg: update EAPI 6 -> 7'

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

        # treeclean
        shutil.rmtree(pjoin(git_repo.path, 'cat/pkg'))
        assert commit() == 'cat/pkg: treeclean'

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
                assert mo.group('holder') == 'Gentoo Authors'

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
