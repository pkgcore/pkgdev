=============
Release Notes
=============

pkgdev 0.2.18 (unreleased)
--------------------------

**pkgdev bugs:**

- bugs: fix a "circular dependency" error part way through filing, when one
  existing bug was matched by several nodes and a dependency path ran between
  them, which made that bug depend on a bug depending on it. Nodes matched to
  the same bug are now merged into one, and a bug kept as a node's own bug is
  no longer obsoleted by another (Arthur Zamarin, #229)

- bugs: a bugzilla error while filing now names the bug and packages being
  filed or modified (Arthur Zamarin, #229)

- bugs: fix ``--filter-stablereqs`` collapsing a target which matches several
  packages down to a single one (Arthur Zamarin)

- bugs: under ``--filter-stablereqs``, when pulling dependency, prefer the
  version the stablereq check considers due for stabilization if possible
  (Arthur Zamarin)

**pkgdev showkw:**

- showkw: drop the vendored copy of tabulate in favor of a dependency on
  ``tabulate>=0.9.0`` (Arthur Zamarin)

pkgdev 0.2.17 (2026-08-14)
--------------------------

**Fixes:**

- config: fix the user config file being silently ignored when
  ``XDG_CONFIG_HOME`` is set to an empty (or relative) value, as is common in
  containers and root shells. Per the XDG basedir spec such values are now
  discarded in favor of ``~/.config`` (Arthur Zamarin, #179)

**pkgdev manifest:**

- manifest: a run with nothing to do now says so instead of printing nothing
  at all, and ``-v`` names each package passed over and why (Arthur Zamarin,
  #80, #108)

- manifest: any file inside a package directory now manifests that package,
  so ``pkgdev manifest files/some.patch`` works rather than failing to parse
  the path as an atom (Arthur Zamarin, #80)

**pkgdev bugs:**

- bugs: bash and zsh completion now complete stabilization groups as targets,
  offered once the leading ``@`` is typed (Arthur Zamarin)

- bugs: fix obsoleting an older bug being a noop when an exact match bug was
  found for the same node, when merging nodes, or when no new bugs were left
  to file (Arthur Zamarin)

- bugs: fix dependencies of a package matched to an already open bug never
  being filed, although they were counted in the bugs to create. Those bugs
  are now filed and added to the existing bug's dependencies (Arthur Zamarin)

- bugs: fix every alternative of an unsolvable ``|| ( ... )`` dependency being
  pulled into the graph, as pkgcheck reports all of its atoms although solving
  any one of them is enough. A single alternative is now taken: one already
  being handled, failing that one already keyworded on the arch, failing that
  the ebuild's own first choice (Arthur Zamarin, #225)

- bugs: fix a dependency being requested on an arch it is already stable on.
  The use deps which made it unsolvable are dropped before matching a version,
  so the closest match won, although the check had just reported it doesn't
  solve the dependency there. Versions already stable (or already keyworded,
  for a keywordreq) on a failing arch are now passed over (Arthur Zamarin, #186)

**pkgdev commit:**

- commit: a new ``acct-user`` or ``acct-group`` package now gets a summary
  naming the identifier it allocates, ``acct-user/foo: add user 123``, instead
  of the generic ``new package, add 0`` (Arthur Zamarin, #14)

**pkgdev tatt:**

- tatt: fix ebuild ``IUSE`` defaults overriding the profile when deciding a
  package's default USE configuration (Arthur Zamarin, #183)

- tatt: add ``--enable-prefixes`` and ``--disable-prefixes``, USE flag prefixes
  which are always resp. never enabled, in every combination and every mode.
  The profile has the last word: flags it forces on cannot be disabled and
  flags it masks cannot be enabled, both kept as the profile has them with a
  warning. A package whose ``REQUIRED_USE`` admits no combination once the
  options are applied raises an error (Arthur Zamarin, #164)

- tatt: ``--use-combos`` no longer repeats a USE combination it already
  emitted. A package with little to vary is now tested fewer times instead of
  rebuilding the same thing over and over (Arthur Zamarin, #164)

pkgdev 0.2.16 (2026-07-31)
--------------------------

**pkgdev bugs:**

- bugs: support filing keywording (``KEYWORDREQ``) bugs and detecting required
  keywording of stablereq dependencies, chaining the stable bug to depend on the
  keyword bug (Arthur Zamarin, #123)

- bugs: fix ``--edit-graph`` silently dropping the bug for the requested
  target when one of its dependencies already had an open bug (Arthur
  Zamarin, #218)

**pkgdev tatt:**

- tatt: ``--use-combos`` now produces genuinely different USE combinations.
  Flags unconstrained by ``REQUIRED_USE`` were left to the constraint solver,
  which assigns them last, so every combination after the first differed only
  in a handful of them (Arthur Zamarin)

pkgdev 0.2.15 (2026-05-15)
--------------------------

**pkgdev tatt:**

- tatt: remove bogus check for ``src_test`` (Arthur Zamarin, #224)

- tatt: don't add ``USE="test"`` when testing (Arthur Zamarin, #219)

**pkgdev bugs:**

- bugs: support auto adding ``@gentoo.org`` for emails (Arthur Zamarin)

- bugs: batch query for bugs with many packages (Arthur Zamarin, #214)

- bugs: support obsoleting bugs when not exact version matches (Arthur Zamarin,
  #206)

- bugs: show all open matching bugs when scanning for existing bugs (Arthur
  Zamarin, #207)

- bugs: fix open bug search with deps (Joe Kappus, #222)

- bugs: require API key earlier (Arthur Zamarin, #217)

- bugs: confirm with user when only partial stabilization group (Arthur Zamarin,
  #211)

- bugs: flush and query before opening editor (Arthur Zamarin, #204)

- bugs: skip hard masked targets (Arthur Zamarin, #210)

pkgdev 0.2.14 (2026-05-05)
---------------------------

- fix dependencies (Arthur Zamarin)

pkgdev 0.2.13 (2026-05-01)
---------------------------
- tests no longer are sensitive to ``git --config --global`` content (Brian
  Harring)

- ``snakeoil`` compatibility is up to 0.12.  That release removes deprecations that
  pkgdev currently relies upon, but will be addressed avant (Brian Harring)

- ``pytest >= 9.0`` is now required (Brian Harring)


pkgdev 0.2.12 (2025-06-14)
-------------------------

- tatt: support ``--use-combos 0`` (to be used with ``--test``) (Arthur
  Zamarin)

- bugs: when selecting a matching package, prefer those with keywords (Arthur
  Zamarin, #205)

pkgdev 0.2.11 (2024-09-06)
-------------------------

- bash completion: improve path handling (Arthur Zamarin)

- mask: update removal line to match GLEP-84 (Arthur Zamarin)

- mask: support auto filing of last-rite bug & PMASKED bugs (Arthur Zamarin, #187)

- mask: support comma separated bugs for ``-b`` and ``--bug`` (Arthur Zamarin)

- tatt: fix template generating extra empty file (Arthur Zamarin)

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
