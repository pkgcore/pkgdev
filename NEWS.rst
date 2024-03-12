=============
Release Notes
=============

pkgdev 0.2.10 (2024-03-12)
-------------------------

**Fixed bugs:**

- bugs: don't crash when package isn't found in git history (Arthur Zamarin)

- tatt: fix ``required_use`` for packages from bug's package list (Arthur
  Zamarin)

- tatt: test run should be after the use combinations (Arthur Zamarin, #174)

- bash-completion: fix missing args for "bugs" and mistake for "mask" (Arthur
  Zamarin)

pkgdev 0.2.9 (2024-02-08)
-------------------------

- ci: add testing on Python 3.12 and Python 3.13 (Sam James, #126)

**New Features:**

- tatt: pass specific test configuration for the specific package, while not
  affecting the dependencies (Arthur Zamarin, #165)

- tatt: add support for custom ``env`` files (Arthur Zamarin, #165)

- bugs: print bug summary where existing bug is found (Arthur Zamarin, #163)

- bugs: mention age of packages in the bug description (Arthur Zamarin, #140)

- bugs: support ``~/.bugzrc`` for api-key extraction (Arthur Zamarin, #162)

- bugs: add ``--find-by-maintainer`` option, for finding all packages
  maintained by a given maintainer (Arthur Zamarin, #157, #168)

- bugs: add support for filtering targets by ``StableRequest`` results from
  ``pkgcheck scan`` (Arthur Zamarin, #157, #168)

- bugs: allow extending maintainer search by project membership (Arthur Zamarin,
  #157, #168)

- bugs: support editing the resulting graph before filing bugs (Arthur Zamarin,
  #169)

- bugs: indicate why dependencies are being added to the graph (Arthur Zamarin,
  #170)

**Fixed bugs:**

- bugs: prefer using user selected targets over latest dependency (Arthur
  Zamarin, #161)

- bugs: merge stable groups as first step (Arthur Zamarin)

- bugs: handle merging of top level nodes (Arthur Zamarin, #125, #167)

- docs: Add ``intersphinx_mapping`` (Brian Harring, #171)

pkgdev 0.2.8 (2023-09-09)
-------------------------

**New Features:**

- pkgdev bugs: add support for passing a root blocker bug, upon which all top
  level bugs will block (Arthur Zamarin, #139)

- pkgdev bugs: fallback to ``~/.bugz_token`` for api-key  (Arthur Zamarin, #138)

- pkgdev bugs: improve ``--api-key`` description and include appropriate
  warning (Florian Schmaus, #159)

- pkgdev bugs: add support for stabilization groups (Arthur Zamarin, #154)

- pkgdev commit: add support for enabling or disabling gpg signing (Arthur
  Zamarin, #147)

- pkgdev push: ``--ask`` stops for confirmation on warnings too (Arthur Zamarin,
  #152)

**Fixed bugs:**

- pkgdev bugs: truncate too long bug summaries (Arthur Zamarin, #141)

- pkgdev bugs: show correct number of bugs which would be opened (Arthur
  Zamarin, #142)

- pkgdev bugs: do not swallow exceptions when reading ``~/.bugz_token``
  (Florian Schmaus, #158)

pkgdev 0.2.7 (2023-04-22)
-------------------------

**New Features:**

- pkgdev bugs: query for existing open bugs (Arthur Zamarin)

- pkgdev bugs: support piping package list from stdin (Arthur Zamarin, #136)

- git: declare ``PKGDEV=1`` environment variable for git commands (Arthur
  Zamarin, #133)

**Fixed bugs:**

- pkgdev bugs: handle correctly merge on new keywords of starting point (Arthur
  Zamarin)

- pkgdev bugs: fix spelling of agent noun for 'file' (Arsen Arsenović, #135)

- pkgdev bugs: better error message when package not found (Arthur Zamarin,
  #134)

- pkgdev bugs: fix restriction passing to ``find_best_match`` (Arthur Zamarin,
  #131)

pkgdev 0.2.5 (2023-03-11)
-------------------------

**New Features:**

- pkgdev tatt: new tool for package testing (Arthur Zamarin, #109)

- pkgdev bugs: new tool for filing stable bugs (Arthur Zamarin, #113)

  This tool is currently *very experimental* and breakage should be expected.
  Use very carefully and monitor created bugs!

- commit: use same summary when matching across multiple ebuilds (Arthur
  Zamarin, #116)

**Fixed bugs:**

- commit: enable ``-e`` usage with ``-M`` or ``-m`` (Arthur Zamarin)

- commit: generate commit title for commit related files only (Arthur Zamarin,
  #122)

pkgdev 0.2.4 (2022-11-26)
-------------------------

- commit: don't show disable for python targets that are disabled (Arthur
  Zamarin)

- commit: mention ``-e`` as nice option (Arthur Zamarin)
  https://bugs.gentoo.org/846785

- Use flit with custom wrapper as build backend (Arthur Zamarin, #104)

- showkw: use color 90 instead of 30 (Arthur Zamarin)

- cli: add support to disable colors using environment variable ``NOCOLOR``
  (Arthur Zamarin)

- push: add ``--pull`` option to auto pull and rebase latest changes from
  remote before scanning and pushing (Arthur Zamarin, #105)

pkgdev 0.2.3 (2022-10-14)
-------------------------

- mask: fix unrelated addition of trailing whitespace (Arthur Zamarin, #98)

- commit: add ``--distdir`` for manifest operations (Arthur Zamarin, #99)

- manifest: better handling of path target (Arthur Zamarin, #85)

pkgdev 0.2.2 (2022-09-20)
-------------------------

- config: fix loading with ``XDG_CONFIG_HOME`` is defined (Arthur Zamarin, #73)

- enable Python 3.11 (Sam James, #81)

- mask: improve parsing of empty header line (Arthur Zamarin, #87)

- mask: improve parsing of empty header line (Arthur Zamarin, #87)

- config: add support for per repo configuration (Arthur Zamarin, #92)

- fix issues with tests for masking with VISUAL set (Arthur Zamarin, #93)

pkgdev 0.2.1 (2022-05-21)
-------------------------

- pkgdev commit: **BREAKING-CHANGE** disable sign-off by default (Arthur
  Zamarin, #68)

- pkgdev: add configuration support. For more info look at [#]_.  (Arthur
  Zamarin, #48, #62)

- pkgdev commit: new summary for stabilizing ALLARCHES (Arthur Zamarin, #61)

- pkgdev mask: offer to send last-rite message email to gentoo-dev ML when
  last-riting a package (Arthur Zamarin, #63)

- pkgdev manifest: add ``--if-modified`` - restrict manifest targets to those
  having uncommitted modifications (Arthur Zamarin, #66)

- pkgdev manifest: add ``--ignore-fetch-restricted`` - skip fetch restricted
  ebuilds (Arthur Zamarin, #67)

.. [#] https://pkgcore.github.io/pkgdev/man/pkgdev.html#config-file-support

pkgdev 0.2.0 (2022-04-10)
-------------------------

- pkgdev commit: Mangle copyright header from single year into year range when
  appropriate (thanks to Thomas Bracht Laumann Jespersen, #49)

- pkgdev commit: Always sort KEYWORDS via mangler (Arthur Zamarin, #47)

- pkgdev commit: For new packages, include version in commit message ("new
  package, add ${PV}") (Arthur Zamarin, #53)

- pkgdev mask: Extend mask comment template (thanks to Thomas Bracht Laumann
  Jespersen, #56)

- pkgdev mask: Accept -b/--bug for referencing bugs (thanks to Thomas Bracht
  Laumann Jespersen, #56)

pkgdev 0.1.9 (2021-07-31)
-------------------------

- pkgdev commit: Revert copyright mangling to previous behavior.

pkgdev 0.1.8 (2021-07-28)
-------------------------

- pkgdev commit: Replace entire copyright date range for new files.

- pkgdev commit: Fix summary generation for certain rename conditions.

pkgdev 0.1.7 (2021-06-29)
-------------------------

- pkgdev commit: Add all matching pkg versions to historical repo (#40).

- pkgdev commit: Use ``git diff-index`` instead of ``git diff`` to avoid config
  settings affecting output.

pkgdev 0.1.6 (2021-06-11)
-------------------------

- pkgdev showkw: Add bash completion support (#38).

- pkgdev commit: Generate summaries for package changes with profile updates,
  e.g. renaming a package and updating profiles/updates in the same commit.

- pkgdev commit: Avoid crash when footer content exists with no summary
  template (#39).

- pkgdev commit: Add initial support for generating summaries from bash diffs.
  For example, this allows automatic summaries to be generated for simple
  PYTHON_COMPAT changes.

pkgdev 0.1.5 (2021-06-03)
-------------------------

- Fix historical repo creation for eclass sourcing.

- Add initial bash completion support.

pkgdev 0.1.4 (2021-05-25)
-------------------------

- pkgdev show: Analog to eshowkw from gentoolkit migrated from pkgcore's
  pshowkw.

- pkgdev manifest: Add -d/--distdir option for custom DISTDIR.

- pkgdev mask: Change removal format to a 'tag: value' style.

pkgdev 0.1.3 (2021-03-26)
-------------------------

- pkgdev mask: Initial implementation of package.mask mangling support.

- pkgdev commit: Allow -s/--scan to accept an optional boolean arg for
  consistency.

- pkgdev commit: Support partial package manifesting (#33).

- pkgdev commit: Add -T/--tag option to add generic commit tags.

pkgdev 0.1.2 (2021-03-19)
-------------------------

- pkgdev commit: Support pulling historical data from unconfigured repos.

- Add initial zsh completion support (#16).

pkgdev 0.1.1 (2021-03-12)
-------------------------

- Replace --ignore-failures option with -A/--ask for ``pkgdev commit`` and
  ``pkgdev push``.

- pkgdev push: Drop explicitly enabled --signed option for gentoo repo (#27).

- pkgdev commit: Add support for -b/--bug and -c/--closes options.

- pkgdev commit: Initial support for summary generation for metadata.xml
  changes (#9).

- pkgdev commit: Enabled signed commits and signoffs based on repo metadata
  (#25).

- pkgdev commit: Initial support for generating modify summaries.

- pkgdev commit: Support summary generation for single rename changes that
  don't involve revbumps.

- pkgdev commit: Add -M/--message-template support.

- pkgdev commit: Support multiple -m/--message options similar to ``git
  commit``.

- pkgdev commit: Support generating manifest summaries (#12).

pkgdev 0.1 (2021-03-05)
-----------------------

- Initial release.

- pkgdev commit: Add subcommand wrapping ``git commit`` supporting commit
  message templating, ebuild manifesting, structured file mangling, and commit
  scanning via pkgcheck.

- pkgdev push: Add subcommand wrapping ``git push`` that verifies local commits
  with pkgcheck before pushing them upstream.

- pkgdev manifest: Add subcommand for manifesting ebuilds.
