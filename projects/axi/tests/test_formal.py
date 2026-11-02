"""Check that the bounded proof both passes good RTL and rejects data corruption."""
import shutil
import subprocess

import pytest

from ..tools.formal import check


@pytest.mark.skipif(shutil.which('yosys') is None, reason='Yosys is required for bounded formal checks')
def test_five_channel_bounded_safety_and_negative_control(tmp_path):
    check(tmp_path / 'positive', depth=6)
    with pytest.raises(subprocess.CalledProcessError):
        check(tmp_path / 'negative', depth=6, inject_fault=True)
    log = (tmp_path / 'negative/yosys.log').read_text()
    assert 'proof did fail' in log, log[-2000:]
    assert (tmp_path / 'negative/counterexample.vcd').is_file()


@pytest.mark.skipif(shutil.which('yosys') is None, reason='Yosys is required for bounded formal checks')
def test_error_transaction_counts_ids_and_last(tmp_path):
    check(tmp_path / 'error', depth=8, target='error')
    assert (tmp_path / 'error/reachable.vcd').is_file()
