import os
import re
import shutil
import logging

import httpx
import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from utils.base_response import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "skills")
_cache: list[dict] | None = None


class InstallRequest(BaseModel):
    url: str


def _parse_skill_md(filepath: str) -> dict | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None

    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not frontmatter_match:
        return None

    yaml_text = frontmatter_match.group(1)
    body = content[frontmatter_match.end():].strip()

    try:
        meta = yaml.safe_load(yaml_text)
    except Exception:
        meta = {}

    name = meta.get("name", os.path.basename(os.path.dirname(filepath)))
    display_name = name.replace("-", " ").title()
    description = (meta.get("description") or "").strip()
    body_preview = body[:200].replace("\n", " ")

    return {
        "name": name,
        "display_name": display_name,
        "description": description,
        "license": meta.get("license", ""),
        "body": body,
        "body_preview": body_preview,
    }


def clear_cache():
    """Clear the skills cache so it is reloaded on next access."""
    global _cache
    _cache = None
    logger.info("Skills cache cleared")


def _get_all_skills() -> list[dict]:
    global _cache
    if _cache is not None:
        return _cache
    skills = []
    if os.path.isdir(SKILLS_DIR):
        for entry in os.scandir(SKILLS_DIR):
            if entry.is_dir():
                md_path = os.path.join(entry.path, "SKILL.md")
                if os.path.isfile(md_path):
                    parsed = _parse_skill_md(md_path)
                    if parsed:
                        skills.append(parsed)
    _cache = skills
    logger.info(f"Loaded {len(skills)} skills from {SKILLS_DIR}")
    return skills


def get_skill_body(name: str) -> str | None:
    """Get the markdown body of a skill by name. Used by model_utils for prompt injection."""
    for skill in _get_all_skills():
        if skill["name"] == name:
            return skill["body"]
    return None


@router.get("")
def list_skills():
    return Response(data=_get_all_skills())


@router.get("/{skill_name}")
def get_skill(skill_name: str):
    skills = _get_all_skills()
    for s in skills:
        if s["name"] == skill_name:
            return Response(data=s)
    return Response(code=404, message=f"Skill '{skill_name}' not found")


@router.delete("/{skill_name}")
def delete_skill(skill_name: str):
    """Delete a skill directory by name."""
    safe_name = os.path.basename(skill_name)
    skill_dir = os.path.join(SKILLS_DIR, safe_name)

    if not os.path.isdir(skill_dir):
        return Response(code=404, message=f"Skill '{safe_name}' not found")

    shutil.rmtree(skill_dir)
    clear_cache()
    logger.info(f"Skill '{safe_name}' deleted")
    return Response(message=f"Skill '{safe_name}' deleted successfully")


@router.post("/install")
async def install_skill(req: InstallRequest):
    """Install a skill from a remote Skills Hub install URL.

    Expects a JSON response with structure:
      { "skill": { "name": "..." },
        "install": { "files": [ { "path": "SKILL.md", "content": "..." } ] } }
    """
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="URL is required")

    logger.info(f"Installing skill from: {req.url}")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(req.url, headers={"Accept": "application/json"})
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=400, detail="Request to skill hub timed out")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=400, detail=f"Skill hub returned HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=400, detail=f"Cannot reach skill hub: {e}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Response is not valid JSON")

    # Extract skill metadata
    skill_info = data.get("skill", {})
    skill_name_candidate = skill_info.get("name", "")

    # Extract files to install
    install_data = data.get("install", {})
    files = install_data.get("files", [])
    if not files:
        # fallback: try top-level install.files
        files = data.get("files", [])
    if not files:
        raise HTTPException(status_code=400, detail="No install files found in response")

    # Find SKILL.md in files
    skill_md_content = None
    for f in files:
        if f.get("path", "").endswith("SKILL.md"):
            skill_md_content = f.get("content", "")
            break

    if not skill_md_content:
        raise HTTPException(status_code=400, detail="No SKILL.md found in install files")

    # Extract skill name from YAML frontmatter
    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", skill_md_content, re.DOTALL)
    if frontmatter_match:
        try:
            meta = yaml.safe_load(frontmatter_match.group(1))
            name_from_frontmatter = meta.get("name", "")
            if name_from_frontmatter:
                skill_name_candidate = name_from_frontmatter
        except Exception:
            pass

    if not skill_name_candidate:
        raise HTTPException(status_code=400, detail="Could not determine skill name from response")

    # Sanitize directory name to prevent path traversal
    safe_name = os.path.basename(skill_name_candidate)
    skill_dir = os.path.join(SKILLS_DIR, safe_name)

    # Check if skill already exists
    existing_md = os.path.join(skill_dir, "SKILL.md")
    if os.path.isfile(existing_md):
        logger.warning(f"Skill '{safe_name}' already exists, will be overwritten")

    # Create directory and write SKILL.md
    os.makedirs(skill_dir, exist_ok=True)
    with open(existing_md, "w", encoding="utf-8") as f:
        f.write(skill_md_content)

    # Also write any additional files (e.g., references/ or templates/)
    for f in files:
        file_path = f.get("path", "")
        if file_path == "SKILL.md":
            continue
        content = f.get("content", "")
        if not content:
            continue
        # Prevent path traversal in additional files
        safe_file_path = os.path.normpath(file_path)
        if safe_file_path.startswith("..") or os.path.isabs(safe_file_path):
            logger.warning(f"Skipping file with unsafe path: {file_path}")
            continue
        target = os.path.join(skill_dir, safe_file_path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fw:
            fw.write(content)

    # Clear cache so the new skill appears immediately
    clear_cache()

    # Parse and return the newly installed skill
    parsed = _parse_skill_md(existing_md)
    if parsed:
        logger.info(f"Skill '{safe_name}' installed successfully ({len(files)} files)")
        return Response(data=parsed, message=f"Skill '{safe_name}' installed successfully")
    else:
        logger.warning(f"Skill '{safe_name}' installed but could not parse SKILL.md")
        return Response(message=f"Skill '{safe_name}' installed (parse warning)")
