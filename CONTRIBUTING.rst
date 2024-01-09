Contributing to pkgdev
======================

Thank you for considering contributing to ``pkgdev``! We appreciate your time and
effort in helping us improve our project. This document outlines the guidelines
and steps for contributing to our project.

Code of Conduct
---------------

We expect all contributors to follow `Gentoo's Code of Conduct
<https://wiki.gentoo.org/wiki/Project:Council/Code_of_conduct>`_. Please make
sure to read and understand it before contributing.

How Can I Contribute?
---------------------

There are several ways you can contribute to ``pkgdev``:

- Reporting bugs
- Suggesting enhancements
- Writing code patches
- Improving documentation
- Providing feedback

Reporting Bugs
--------------

If you encounter any bugs or issues while using ``pkgdev``, please report them
by following these steps:

1. Check if the bug has already been reported by searching our `issue tracker
   <https://github.com/pkgcore/pkgdev/issues>`_.
2. If the bug hasn't been reported, open a new issue and provide a clear and
   detailed description of the problem.
3. Include any relevant information, such as error messages, screenshots, or
   steps to reproduce the issue.
4. Assign appropriate labels to the issue (e.g., bug, tool/tatt) and provide
   any additional context that might be helpful.

Suggesting Enhancements
-----------------------

If you have ideas for new features or improvements to ``pkgdev``, we would love
to hear them! To suggest an enhancement, please follow these steps:

1. Check if the enhancement has already been suggested by searching our `issue
   tracker <https://github.com/pkgcore/pkgdev/issues>`_.
2. If the enhancement hasn't been suggested, open a new issue and provide a
   clear and detailed description of your idea.
3. Explain why you think the enhancement would be valuable and how it aligns
   with the project's goals.
4. Assign appropriate labels to the issue (e.g., enhancement, tool/bugs)
   and provide any additional context that might be helpful.

Pull Requests
-------------

We welcome pull requests from contributors. To submit a pull request, please
follow these steps:

1. Fork the repository and create a new branch for your changes.
2. Make your changes and ensure that the code passes all tests.
3. Write clear and concise commit messages that describe your changes.
4. Sign-off your commits, for example using the command ``git commit -s``. Must
   confirm to `GLEP-76 <https://www.gentoo.org/glep/glep-0076.html>`_.
5. Submit a pull request, explaining the purpose and benefits of your changes.
6. Be responsive to any feedback or questions during the review process.

Styleguides
-----------

When contributing to ``pkgdev``, please adhere to the following styleguides:

- Code formatting is done using `black <https://pypi.org/project/black/>`_. You
  can run ``make format`` for it to auto format your files
- While not a hard requirement in all cases, we do want to have a healthy
  coverage of branches and flows. Attempt to write unit tests.

Vulnerabilities reports
-----------------------

In case you have found a vulnerability in ``pkgdev``'s code, feel free to open
an issue with as detailed explanation as possible. We believe in reporting as
fast as possible to our user base, so a vulnerability report should start as
public, even if no fix is ready, in which case we would also report it in extra
channels (i.e. IRC channel and gentoo-dev mailing list).
