"""Generate labelled synthetic schematics and PDF/text input documents, not real evidence."""

import hashlib
import math
import textwrap
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFont

from shared.seed import Snapshot


def asset_path(output: Path, url: str) -> Path:
    target = (output / urlsplit(url).path.lstrip("/")).resolve()
    if not target.is_relative_to(output.resolve()):
        raise ValueError("Asset path escapes the output directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def page(title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (900, 1100), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 900, 85), fill="#12364a")
    draw.text(
        (25, 20), "SYNTHETIC DEMO FIXTURE", fill="white", font=ImageFont.load_default(size=30)
    )
    draw.text((25, 105), title, fill="#12364a", font=ImageFont.load_default(size=24))
    return image, draw


def lines(draw: ImageDraw.ImageDraw, text: str, y: int, *, size: int = 20) -> int:
    font = ImageFont.load_default(size=size)
    for paragraph in text.splitlines():
        for line in textwrap.wrap(paragraph, width=75) or [""]:
            draw.text((25, y), line, fill="#12364a", font=font)
            y += size + 8
    return y


def photo_image(snapshot: Snapshot, photo: dict) -> Image.Image:
    image, draw = page(f"{photo['photoId']} - {photo['checklistItemCode']}")
    overrides = {row["photoId"]: row for row in snapshot.recipe["photoFindings"]}
    overrides.update({row["photoId"]: row for row in snapshot.recipe["scenario"]["replacements"]})
    details = overrides.get(photo["photoId"], {})
    if photo["sector"]:
        program = next(
            row
            for row in snapshot.recipe["programs"]
            if row["projectId"] == snapshot.get("Site", photo["siteId"])["projectId"]
        )
        planned = program["rfdsAzimuth"][photo["sector"]]
        observed = details.get("observedAzimuth", planned)
        draw.ellipse((220, 210, 620, 610), outline="#12364a", width=5)
        for angle, color, radius in ((planned, "#0070c0", 190), (observed, "#c44020", 160)):
            end = (
                420 + math.sin(math.radians(angle)) * radius,
                410 - math.cos(math.radians(angle)) * radius,
            )
            draw.line((420, 410, *end), fill=color, width=8)
            draw.ellipse((end[0] - 8, end[1] - 8, end[0] + 8, end[1] + 8), fill=color)
        lines(
            draw,
            f"NORTH\nSector {photo['sector']}: planned {planned} degrees (blue)\n"
            f"Observed {observed} degrees (orange)\nDeviation {abs(observed - planned)} degrees",
            645,
        )
    elif photo["checklistItemCode"] == "grounding":
        draw.rectangle((160, 350, 740, 410), fill="#b97b41", outline="#12364a", width=4)
        for x in range(220, 720, 100):
            draw.line((x, 405, x, 650), fill="#25804a", width=12)
            draw.ellipse((x - 12, 367, x + 12, 391), fill="#555555")
        if details.get("labelPresent", True):
            draw.rectangle((310, 260, 590, 320), fill="#ffffa0", outline="#12364a", width=3)
            draw.text(
                (320, 275), "GROUND G-01", fill="#12364a", font=ImageFont.load_default(size=22)
            )
        lines(
            draw,
            "Grounding assembly - label "
            + ("present" if details.get("labelPresent", True) else "MISSING"),
            700,
        )
    else:
        draw.rectangle((200, 230, 690, 650), outline="#12364a", width=5)
        for x in (300, 440, 580):
            draw.line((x, 280, x, 600), fill="#25804a", width=14)
            draw.rectangle((x - 42, 390, x + 42, 430), fill="#ffffa0", outline="#12364a")
        lines(
            draw,
            f"Checklist illustration: {photo['checklistItemCode']}\n"
            "Synthetic identifiers and labels visible.",
            700,
        )
    lines(
        draw,
        f"{photo['siteId']} | GPS {photo['latitude']}, {photo['longitude']}\n"
        f"Captured {photo['capturedAt']}\n"
        "SCHEMATIC ONLY - NOT A FIELD PHOTOGRAPH OR MODEL VALIDATION.",
        910,
        size=17,
    )
    return image


def document_text(snapshot: Snapshot, artifact: dict) -> str:
    site = snapshot.get("Site", artifact["siteId"])
    program = next(
        row for row in snapshot.recipe["programs"] if row["projectId"] == site["projectId"]
    )
    body = [
        "SYNTHETIC DEMO INPUT - NOT AN APPROVAL OR REAL SITE RECORD",
        artifact["artifactId"],
        artifact["artifactType"],
        f"Site: {site['siteId']}",
        f"Program: {program['programCode']}",
        f"Version: {artifact['version']}",
        f"Coordinates: {site['latitude']}, {site['longitude']}",
    ]
    if artifact["artifactType"] == "rfds":
        body.extend(
            f"Sector {sector} azimuth {angle} degrees"
            for sector, angle in program["rfdsAzimuth"].items()
        )
        body.append("Compare observed azimuth against these planned values.")
    elif artifact["artifactType"] == "photo_pack":
        body.append("Photo index (includes retained superseded evidence):")
        body.extend(
            photo["photoId"]
            for photo in snapshot.rows("Photo")
            if photo["artifactId"] == artifact["artifactId"]
        )
    elif artifact["artifactType"] == "acceptance_certificate":
        body.append("DRAFT acceptance certificate. Customer acceptance has NOT been granted.")
    else:
        body.append("Synthetic input content for document retrieval and fixture validation.")
    return "\n".join(body) + "\n"


def generate_assets(snapshot: Snapshot, output: Path) -> list[dict]:
    manifest = []
    stamp = datetime.fromisoformat(
        snapshot.recipe["timestamp"].replace("Z", "+00:00")
    ).utctimetuple()
    for photo in snapshot.rows("Photo"):
        target = asset_path(output, photo["blobUrl"])
        with photo_image(snapshot, photo) as image:
            image.save(target, format="PNG")
        manifest.append(
            {"id": photo["photoId"], "path": target.relative_to(output.resolve()).as_posix()}
        )
    for artifact in snapshot.rows("AcceptanceArtifact"):
        target = asset_path(output, artifact["blobUrl"])
        text = document_text(snapshot, artifact)
        image, draw = page(artifact["artifactId"])
        lines(draw, text, 160, size=18)
        image.save(
            target,
            format="PDF",
            title=artifact["artifactId"],
            creationDate=stamp,
            modDate=stamp,
            resolution=72.0,
        )
        image.close()
        target.with_suffix(".txt").write_text(text, encoding="utf-8", newline="\n")
        for path in (target, target.with_suffix(".txt")):
            manifest.append(
                {
                    "id": artifact["artifactId"],
                    "path": path.relative_to(output.resolve()).as_posix(),
                }
            )
    adversarial = snapshot.recipe["adversarialDocument"]
    target = (output / adversarial["path"]).resolve()
    if not target.is_relative_to(output.resolve()):
        raise ValueError("Adversarial fixture path escapes the output directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(adversarial["content"], encoding="utf-8", newline="\n")
    manifest.append(
        {
            "id": adversarial["id"],
            "path": target.relative_to(output.resolve()).as_posix(),
        }
    )
    for entry in manifest:
        entry["sha256"] = hashlib.sha256((output / entry["path"]).read_bytes()).hexdigest()
    return manifest
