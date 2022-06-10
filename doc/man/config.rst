Config file support
===================

Config files are supported by most subcommands of ``pkgdev`` from any of three
locations. Listed in order of increasing precedence these include the
following:

- system config -- ``/etc/pkgdev/pkgdev.conf``
- user config -- ``${XDG_CONFIG_HOME}/pkgdev/pkgdev.conf``
- user config -- ``~/.config/pkgdev/pkgdev.conf``
- custom config -- specified via the ``--config`` option

Any settings from a config file with higher precedence will override matching
settings from a config file with a lower precedence, e.g. user settings
override system settings. Note that command line options override any matching
config file setting.

In terms of file structure, basic INI formatting is required and allows
creating a default section (DEFAULT) for system-wide settings or repo-specific
sections. The INI key-value pairs directly relate to the available
long-options supported by the various prefixed by the subcommand name and their
related values. To find all possible configuration options, run:
``pkgdev {subcommand} --help``. See the following examples for config settings:

- Run ``pkgcheck scan`` before committing and asks for confirmation (instead of
  aborting) when creating commits with QA errors::

    [DEFAULT]
    commit.scan = true
    commit.ask = true

- Allow pushing commits with QA errors, but only for the 'gentoo' repository::

    [gentoo]
    push.ask = true

- Add `Signed-off-by` consenting to the `Certificate of Origin
  <https://www.gentoo.org/glep/glep-0076.html#certificate-of-origin>`_
  to all commits::

    [DEFAULT]
    commit.signoff = true

- When committing, stage all files in current working directory (note that this
  option doesn't expect value, therefore no value is defined post equal sign)::

    [DEFAULT]
    commit.all =

- All previous config settings combined::

    [DEFAULT]
    commit.scan = true
    commit.ask = true
    commit.all =

    [gentoo]
    push.ask =
