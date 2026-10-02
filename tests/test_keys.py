"""The key decoder, fed characters. No terminal."""
from __future__ import annotations

import pytest

from operator_cli import keys


def _pull(chars):
    pending = list(chars)

    def pull():
        return pending.pop(0) if pending else ""

    return pull


def _posix(chars, ready_flags):
    flags = list(ready_flags)

    def ready(timeout):
        assert timeout == keys.ESC_WAIT
        return flags.pop(0)

    return list(keys.decode(_pull(chars), windows=False, ready=ready))


def test_windows_arrows_from_both_prefixes():
    assert list(keys.decode(_pull(["\xe0", "H", "\x00", "P"]), windows=True)) == [
        "up", "down"]
    assert list(keys.decode(_pull(["\x00", "H", "\xe0", "P"]), windows=True)) == [
        "up", "down"]


def test_posix_arrows_from_csi_and_ss3():
    assert _posix(["\x1b", "[", "A", "\x1b", "O", "B"], [True, True, True, True]) == [
        "up", "down"]


def test_a_bare_esc_is_not_an_arrow():
    assert _posix(["\x1b"], [False]) == ["esc"]


def test_esc_then_a_letter_is_esc_and_the_letter():
    assert _posix(["\x1b", "a"], [True]) == ["esc", "a"]


def test_ctrl_c_raises_on_both_encodings():
    with pytest.raises(KeyboardInterrupt):
        list(keys.decode(_pull(["\x03"]), windows=True))
    with pytest.raises(KeyboardInterrupt):
        list(keys.decode(_pull(["\x03"]), windows=False, ready=lambda _t: False))


def test_backspace_enter_space_and_a_letter():
    raw = ["\x08", "\x7f", "\r", "\n", " ", "q"]
    assert list(keys.decode(_pull(raw), windows=True)) == [
        "backspace", "backspace", "enter", "enter", "space", "q"]
    assert list(keys.decode(_pull(raw), windows=False, ready=lambda _t: False)) == [
        "backspace", "backspace", "enter", "enter", "space", "q"]


def test_an_unknown_scan_code_is_swallowed():
    assert list(keys.decode(_pull(["\xe0", "M", "z"]), windows=True)) == ["z"]


def test_enable_vt_does_not_run_off_windows(monkeypatch):
    monkeypatch.setattr(keys.os, "name", "posix")
    assert keys.enable_vt() is None


def test_termios_is_restored_when_the_body_is_interrupted():
    saved = ["old"]
    restored = []
    set_cbreak = []

    class Termios:
        TCSADRAIN = 1

        @staticmethod
        def tcgetattr(fd):
            assert fd == 7
            return saved

        @staticmethod
        def tcsetattr(fd, when, mode):
            restored.append((fd, when, mode))

    class Tty:
        @staticmethod
        def setcbreak(fd):
            set_cbreak.append(fd)

    def body():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        keys.guard_termios(7, Termios, Tty, body)
    assert set_cbreak == [7]
    assert restored == [(7, Termios.TCSADRAIN, saved)]
