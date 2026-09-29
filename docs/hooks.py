"""Hooks for the documentation.

Injects root-level Markdown files (README, CHANGELOG, LICENSE) into the
mkdocs site so the GitHub repository and the published docs stay in sync
from a single source. The README is mounted as ``index.md`` (the docs
landing); CHANGELOG and LICENSE keep their canonical names.

Also rewrites ``docs/...`` paths in the README so internal links that
resolve correctly on GitHub (where ``README.md`` sits next to the
``docs/`` folder) also resolve correctly inside the docs site (where
``README.md`` is the index, sitting alongside its siblings).
"""

from __future__ import annotations

import html
import pathlib
import re
from pathlib import Path
from typing import TYPE_CHECKING

from mkdocs.structure.files import File, Files

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.structure.pages import Page


# ---------------------------------------------------------------------------
# Monkeypatch mkdocs-jupyter's ``should_include`` so that ``.md`` files that
# are *not* in the plugin's ``include`` glob list are rejected before
# ``jupytext.read()`` is called. Without this, jupytext invokes Pandoc
# (``--from markdown --to ipynb``) for every ``.md`` file it encounters and
# Pandoc emits dozens of "Div ... unclosed" warnings for files that contain
# raw HTML or mkdocstrings ``:::`` directives (the rendered API page being
# the worst offender). The warnings are harmless but loud and pollute every
# ``mkdocs build`` log; this patch silences them at the source.
# ---------------------------------------------------------------------------
try:
    from mkdocs_jupyter.plugin import Plugin as _JupyterPlugin

    _orig_should_include = _JupyterPlugin.should_include

    def _patched_should_include(self, file):  # type: ignore[override]
        ext = pathlib.PurePath(file.abs_src_path).suffix
        if ext == ".md":
            srcpath = pathlib.PurePath(file.abs_src_path)
            if not any(srcpath.match(p) for p in self.config["include"]):
                return False
        return _orig_should_include(self, file)

    _JupyterPlugin.should_include = _patched_should_include
except ImportError:
    pass


_ROOT = Path(__file__).parent.parent

_README = _ROOT / "README.md"
_CHANGELOG = _ROOT / "CHANGELOG.md"
_LICENSE = _ROOT / "LICENSE"


def on_files(files: Files, config: MkDocsConfig) -> Files:
    """Inject root-level files as docs pages.

    - ``README.md`` becomes ``index.md`` (the docs landing).
    - ``CHANGELOG.md`` is appended as ``CHANGELOG.md``.
    - ``LICENSE`` is appended as ``LICENSE.md`` so it renders with the
      Markdown extension Material expects.
    """
    # Remove docs/index.md if it exists so README.md takes its place.
    files = Files([f for f in files if f.src_path != "index.md"])

    idx = File(
        path="index.md",
        src_dir=str(_README.parent),
        dest_dir=str(config.site_dir),
        use_directory_urls=config.use_directory_urls,
    )
    idx.abs_src_path = str(_README)
    files.append(idx)

    changelog = File(
        path="CHANGELOG.md",
        src_dir=str(_CHANGELOG.parent),
        dest_dir=str(config.site_dir),
        use_directory_urls=config.use_directory_urls,
    )
    changelog.abs_src_path = str(_CHANGELOG)
    files.append(changelog)

    lic = File(
        path="LICENSE.md",
        src_dir=str(_LICENSE.parent),
        dest_dir=str(config.site_dir),
        use_directory_urls=config.use_directory_urls,
    )
    lic.abs_src_path = str(_LICENSE)
    files.append(lic)

    return files


# Regex matching src="docs/..." in HTML img tags and ](docs/...) in markdown.
_DOCS_PREFIX = re.compile(r'((?:src="|]\())(docs/)')

# Regex matching ](LICENSE) markdown links and src="LICENSE" image refs.
# The README points at the bare ``LICENSE`` file because that is where the
# file lives in the repository root; inside the docs site the hook above
# injects ``LICENSE`` as ``LICENSE.md`` so the link target must follow.
_LICENSE_LINK = re.compile(r'((?:src="|]\())LICENSE(?=["\)])')


def on_page_markdown(markdown: str, page: Page, **_kwargs: object) -> str:
    """Rewrite README-only link targets so they resolve in mkdocs.

    On GitHub the README sits next to the ``docs/`` folder and the bare
    ``LICENSE`` file, so links like ``docs/installation.md`` and
    ``LICENSE`` resolve naturally. Inside the docs site the README is
    the index page already inside ``docs/``, the ``docs/`` prefix must
    be stripped, and ``LICENSE`` is mounted at ``LICENSE.md``.

    ``LICENSE`` is plain text, so it renders verbatim; as Markdown, its
    ``[...]`` placeholders would parse as reference links.
    """
    if page.file.abs_src_path == str(_LICENSE):
        return f'<pre style="white-space: pre-wrap">{html.escape(markdown)}</pre>\n'
    if page.file.abs_src_path == str(_README):
        markdown = _DOCS_PREFIX.sub(r"\1", markdown)
        markdown = _LICENSE_LINK.sub(r"\1LICENSE.md", markdown)
    return markdown
