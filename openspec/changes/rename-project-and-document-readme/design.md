# Design

## Context

The original `YOLO11-seg` remote redirects to the same private repository now named `-Agent`. GitHub repository names do not support the requested Chinese title. The checkout has no root README and contains ignored local knowledge libraries and generated release assets.

## Decisions

- Use `结构损伤智能分析Agent` as the README title and include it in the repository description. Use a compatible ASCII repository slug, with `structural-damage-analysis-agent` as the suggested name.
- Describe behavior from implementation and model metadata. Distinguish committed source and Git LFS assets from private settings, production indexes and packaged embedding weights.
- Verify documentation links, documented command options and this change's OpenSpec metadata before committing. Check the repository identity, visibility, remote HEAD and README after publishing.

## Migration

Rename the existing repository without replacing it or changing visibility. Point `origin` to the canonical renamed HTTPS URL, then publish the documentation commit to `main`. Repository history remains in the existing repository; the local workspace directory is retained.
