"""Shared simulator environment and pytest-owned build directories."""
import os
from pathlib import Path
from shutil import which

import pytest
from . import simulation


def pytest_configure(config):
    # Keep compiler caches shared across projects; respect caller overrides.
    root = Path(__file__).resolve().parents[1]
    os.environ.setdefault('CCACHE_DIR', str(root / 'build/ccache'))
    if which('ccache') is not None:
        os.environ.setdefault('OBJCACHE', 'ccache')
    make_flags = os.environ.get('MAKEFLAGS', '')
    if 'OPT_FAST=' not in make_flags:
        os.environ['MAKEFLAGS'] = (make_flags + ' OPT_FAST=-O0').strip()


@pytest.fixture(scope='session', autouse=True)
def simulator_build_dirs(tmp_path_factory):
    simulation._build_directory = tmp_path_factory.mktemp
    try:
        yield
    finally:
        simulation._build_directory = None
