=============
Release Notes
=============

pkgdev 0.1 (2021-03-05)
-----------------------

- Initial release.

- pkgdev commit: Add subcommand wrapping ``git commit`` supporting commit
  message templating, ebuild manifesting, structured file mangling, and commit
  scanning via pkgcheck.

- pkgdev push: Add subcommand wrapping ``git push`` that verifies local commits
  with pkgcheck before pushing them upstream.

- pkgdev manifest: Add subcommand for manifesting ebuilds.
