"""Generate a timed HTML composition using Hyperframes' documented clip format."""

from __future__ import annotations

from html import escape


def build_composition(title: str, scenes: list[dict], audio_filename: str, duration: float,
                      width: int = 1080, height: int = 1920) -> str:
    if not scenes or duration <= 0:
        raise ValueError("composition requires scenes and a positive audio duration")
    visual_clips = []
    for index, scene in enumerate(scenes):
        start = float(scene["start_seconds"])
        end = float(scene["end_seconds"])
        if start < 0 or end <= start or end > duration + 0.05:
            raise ValueError("scene timing must fit within the source audio")
        text = escape(str(scene.get("text_overlay") or scene.get("narration") or ""))
        visual = escape(str(scene.get("visual_description") or ""))
        description = f'<p class="visual-description">{visual}</p>' if visual else ""
        visual_clips.append(
            f'<section class="clip scene" id="scene-{index + 1}" data-start="{start:.3f}" '
            f'data-duration="{end - start:.3f}" data-track-index="1">'
            f'<div class="scene-card">{description}<h1>{text}</h1></div></section>'
        )
    return f'''<!doctype html>
<html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;background:#061A2B;color:#F3F6FA;font-family:Arial,sans-serif}}
#root{{position:relative;width:100%;height:100%;overflow:hidden;background:#061A2B}}
.scene{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:8%;text-align:center}}
.scene-card{{max-width:90%}}h1{{font-size:7vw;line-height:1.12;margin:0;text-shadow:0 3px 24px #0008}}
.visual-description{{font-size:2vw;color:#9ADCF0;margin-bottom:2em}}
</style></head><body>
<main id="root" data-composition-id="{escape(title, quote=True)}" data-start="0" data-width="{int(width)}" data-height="{int(height)}">
<audio class="clip" id="narration-audio" data-start="0" data-duration="{float(duration):.3f}" data-track-index="0" src="{escape(audio_filename, quote=True)}"></audio>
{''.join(visual_clips)}
</main></body></html>'''
