"""Voice-to-voice conversion: OpenVoice v2 tone-color converter (free, local, MIT licensed).

Takes your own recorded performance (timing/emotion preserved) and re-paints its timbre
to match a reference voice clip.
"""
from __future__ import annotations

from pathlib import Path


class VoiceChangeError(RuntimeError):
    pass


def _trust_silero_vad() -> None:
    """OpenVoice's VAD step calls torch.hub.load on snakers4/silero-vad, which otherwise
    blocks on an interactive y/N prompt the first time — pre-trust it so it never blocks."""
    import torch.hub as hub

    hub_dir = Path(hub.get_dir())
    hub_dir.mkdir(parents=True, exist_ok=True)
    fp = hub_dir / "trusted_list"
    fp.touch(exist_ok=True)
    entries = set(fp.read_text().splitlines())
    if "snakers4_silero-vad" not in entries:
        entries.add("snakers4_silero-vad")
        fp.write_text("\n".join(sorted(entries)) + "\n")


def change_voice(src_wav: Path, ref_wav: Path, out_wav: Path) -> None:
    """Re-voice src_wav (your recording) to sound like ref_wav (a short reference clip)."""
    _trust_silero_vad()
    try:
        from openvoice_cli.__main__ import tune_one
        tune_one(input_file=str(src_wav), ref_file=str(ref_wav), output_file=str(out_wav), device="cpu")
    except ImportError:
        raise VoiceChangeError("Voice changer not installed (run ./start.sh --voice-change)")
    except Exception as e:  # noqa: BLE001
        raise VoiceChangeError(str(e))
