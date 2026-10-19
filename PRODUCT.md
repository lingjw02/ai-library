# AI Library

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users and purpose

Ling uses a local knowledge library to retain original notes, imported conversation sources and reusable AI memories. The primary task is to find, read and organize these together while preserving their distinct authorship, revision and review rules.

## Operating context

Python / FastAPI and SQLite backend; browser frontend in native JavaScript. External Agents connect through MCP; embedding generation stays outside the backend. This redesign does not change that architecture. Current implementation and limitations are in README.md and PLAN_PHASES.md.

## Confirmed commitments

User approved the complete Frontend v1.4 proposal on 2026-10-11: freely rotatable 3D universe; suns as topics; planets as individual content or nested topics; Notes and Memory mixed by topic with explicit labels; the separate type switch was subsequently removed by user request; persistent topic creation, rename and membership. Removing membership must retain original content. Reading pauses automatic motion. Navigation, search and an accessible directory remain available; maintenance retains its existing workflows in a secondary entry.

## Constraints and evidence

Do not invent records, semantic relationships, verified status or quantity claims. Existing data includes conversation sources and inferred, unverified memories. Same content can be referenced by multiple collections without copying. Respect current scope and optimistic revision protection. No Git commits, destructive cleanup, paid services or personal client configuration changes are authorized by this redesign.

## Design authority

work/AI-Library-Frontend-README-v1.4.md supersedes the older separate-tab visual concept. Its full proposed interaction was explicitly approved; implementation and acceptance must be recorded separately.

## Latest visual reference and acceptance

On 2026-10-11 the user specified Neural Vault (https://github.com/useroneoneone/neural-vault). v1.8 adapts its black canvas, wireframe jellyfish, glowing core and floating controls while keeping actual library data and 3D navigation. The user subsequently said the overall presentation is not yet ideal. Visual/interaction acceptance is REVIEW_REQUIRED; the next direction is pending clarification. The fixed Notes hub, installed Skill names and explicit exit remain confirmed functionality.
