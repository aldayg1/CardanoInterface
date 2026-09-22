# Roadmap

CardanoInterface is a local, sovereign Cardano wallet tool. This roadmap covers planned work for the next 12 months under the Intersect Maintainer Retainer Program (MRP). Dates are estimates and may shift based on community feedback, testing, and Cardano protocol upgrades.

## Primary Focus: Maintenance

The MRP is a maintenance contract. The primary deliverables are keeping the project healthy, secure, and compatible with Cardano upgrades. Feature development is secondary.

### What maintenance means

- Bug fixes and issue triage
- Security patches and dependency updates
- Hardfork compatibility (Cardano node upgrades)
- Documentation improvements
- PR reviews and community support
- Governance compliance (SECURITY.md, CONTRIBUTING.md, license)

## Q1 2027 — Establishment

**Primary (70-80%)**
- 30-day evaluation period
- Issue triage and backlog review
- Documentation updates (README, SECURITY.md, CONTRIBUTING.md)
- Bug fixes and dependency updates
- Governance compliance setup

**Secondary (20-30%)**
- Resilient transaction submission (retry, failover, persist-before-submit queue)

## Q2 2027 — Hardfork Compatibility

**Primary (70-80%)**
- Hardfork compatibility (if applicable)
- Security patches
- PR reviews and community support
- Issue resolution

**Secondary (20-30%)**
- Token operations (multi-asset sends + CIP-25 minting)
- UTxO tools (consolidation and fragmentation)
- Address verification (on-chain validation, collision detection)

## Q3 2027 — dApp Connector

**Primary (70-80%)**
- Continued maintenance
- Documentation improvements
- Community engagement and support

**Secondary (20-30%)**
- CIP-30 dApp connector (browser extension for dApp interactions)

## Q4 2027 — Annual Review

**Primary (70-80%)**
- Continued maintenance
- Annual review preparation
- Documentation finalization

**Secondary (20-30%)**
- Portfolio view (aggregate balances, fiat valuation)

## Quarterly Reporting

Each quarter, a markdown-based task report is submitted alongside automated dashboard data. Tasks are aligned with hardforks, upgrades, and community needs.

## Principles

- **Maintenance first.** MRP pays for reliability, not features.
- **Ship what works.** Features are delivered when they pass real transaction testing on Preprod, not before.
- **Modest scope.** Each quarter targets focused deliverables. Features that don't make the cut roll to the next quarter.
- **Community-driven.** Priorities shift based on user feedback. Open an issue to suggest or vote on features.
