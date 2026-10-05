# Ren End User License Agreement

> **DRAFT FOR LEGAL REVIEW - NOT APPROVED FOR DISTRIBUTION OR EFFECTIVE.**
> This working draft is not an offer, does not create an agreement, and is
> not wired to an acceptance flow. Do not publish or present it as binding
> until counsel approves the terms, the licensor is identified, and the
> product's third-party licensing questions below are resolved.

Draft date: October 4, 2026

Proposed licensor: **[LEGAL ENTITY AND JURISDICTION TO BE COMPLETED]**

## Questions for counsel before approval

1. Who is the contracting licensor, where is it established, and what contact
   address and notices process should appear here?
2. Which countries and customer types will the public edition serve? What
   governing law, venue, consumer rights, age limits, export controls, and
   dispute terms apply in those places?
3. What are the approved license scope, user/device limits, price, duration,
   renewal, and termination terms? Is commercial use intended to be allowed
   once component rights are cleared?
4. Resolve the open component issues recorded in
   [`THIRD_PARTY_NOTICES`](../../THIRD_PARTY_NOTICES) before approving a
   public build. In particular, confirm the path forward for Remotion's
   Automators terms, InsightFace's `buffalo_l` weights, Audio Flamingo Next,
   and Desert Ant/Voz; review the Praat-Parselmouth GPLv3 distribution
   implications and the Gemma redistribution notice and use restrictions.
   Should any capability be disabled, replaced, or separately licensed?
5. What rights should this agreement grant in user inputs and outputs, and
   how should it address third-party model outputs, copyright uncertainty,
   privacy/publicity rights, and similarity to other outputs?
6. Approve the warranty disclaimer, liability exclusions and cap, any
   indemnities, and any exceptions required by applicable law.
7. What notice and affirmative assent are required before distribution, and
   how should changes to these terms be handled? This draft adds no acceptance
   behavior.

## Proposed agreement

### 1. Draft status and scope

This section is proposed language for the public edition of Ren, the video
editing software and related documentation (the **Software**). It is not
effective while this document is marked DRAFT. It does not itself authorize
distribution or use of any component whose separate terms do not permit that
use.

If approved and offered with an effective agreement, the agreement would be
between **[LEGAL ENTITY]** (**Licensor**, **we**, or **us**) and the person or
organization that accepts it (**you**). The final agreement must identify its
effective date and acceptance method.

### 2. Proposed license

Subject to this agreement and all applicable third-party terms, Licensor
proposes to grant you a limited, non-exclusive, non-transferable license to
install and use the approved public edition of the Software on devices you
control for your personal or internal business work. Any paid, seat, device,
or duration limits must be stated in the release offer or an order form.

This grant would apply only to a build whose included and enabled components
have been cleared for the distribution and use being offered. It does not
override a third-party restriction, supply a missing third-party license, or
authorize a use that a component's own terms prohibit. Third-party components
remain subject to their separate licenses and notices.

Except for rights expressly granted in the final agreement or a component's
own license, you may not copy, modify, distribute, sublicense, sell, rent, or
make the Software available as a hosted service. Nothing here limits rights
that applicable law does not allow Licensor to restrict.

### 3. Your projects and content

As between you and Licensor, you retain the rights you have in the video,
audio, images, text, project files, instructions, and other material you
provide or use with the Software (**Input Content**). You give Licensor no
ownership interest in Input Content under this draft. The Software may read,
analyze, edit, render, or store Input Content locally to perform the actions
you request.

You are responsible for having the rights and permissions needed to use your
Input Content, including permissions concerning people depicted or heard,
music, locations, and other third-party material. You are also responsible
for reviewing the Software's edits and exports before using or sharing them.

### 4. Outputs

As between you and Licensor, this draft does not claim ownership of exports
created from your project. Any rights in an output depend on your rights in
its inputs, applicable law, and the separate terms of any model or service
used to help create it. Licensor does not promise that an output is unique,
copyrightable, cleared for a particular use, or free of third-party rights.

### 5. Agent harnesses and other third-party services

Ren prepares some model requests in local project files for the agent harness
you selected. Some still-image inspection paths invoke your installed Codex
CLI using its existing authentication. Ren does not provide or operate those
model accounts. If you choose to use an agent harness or model provider, its
terms and privacy practices govern its services and any content you direct it
to process. You decide whether to use those services and are responsible for
reviewing their terms and settings.

DaVinci Resolve, agent harnesses, downloaded models, package registries, and
other separately supplied or accessed components may have their own terms.
The inventory and attribution record is in
[`THIRD_PARTY_NOTICES`](../../THIRD_PARTY_NOTICES). That inventory is not a
replacement for each component's license text.

### 6. No automatic support upload

Ren does not automatically send project files, logs, or support archives to
Licensor. If you choose to send diagnostic files or a support bundle to
someone, that recipient receives the files you choose to share. Review their
contents first; they may include project paths, prompts, context, logs, or
other sensitive material.

### 7. Updates and termination

Any final agreement should state whether updates are included and how their
terms may differ. You may stop using the Software at any time. Licensor may
terminate a final license for a material breach after any cure period
required by law or stated in the approved agreement. Ending a license does
not itself delete your projects, footage, or exports; local files remain
under your control. Component licenses may have separate termination rules.

### 8. Warranty and liability

**Counsel must replace or approve this section before publication.** To the
maximum extent permitted by applicable law, the approved agreement may
disclaim implied warranties and limit liability for indirect, incidental,
special, consequential, or punitive damages. Any aggregate liability cap,
exceptions, consumer protections, and mandatory remedies must be set by
counsel for the selected jurisdictions. Nothing in the final agreement
should exclude a right or remedy that cannot lawfully be excluded.

### 9. General terms

The final agreement should identify the controlling language, governing law,
notice method, and dispute venue after counsel selects the applicable
jurisdictions. It should also address assignment, severability, waiver, and
the relationship between this agreement and third-party component terms.
Until those details are completed and an acceptance method is approved, this
document remains a non-binding draft.

## Third-party attribution and license record

The current component names, sources, versions, license records, and known
commercial-use questions are maintained in
[`THIRD_PARTY_NOTICES`](../../THIRD_PARTY_NOTICES). That record includes,
among other items, Praat-Parselmouth (GPLv3), Remotion (Remotion Company
License), the Gemma model terms, InsightFace `buffalo_l`, Audio Flamingo
Next, Desert Ant/Voz, Montserrat (SIL Open Font License 1.1), and GSAP
(GSAP Standard License). Preserve the applicable upstream notices with any
distribution. The inventory is generated and must be rechecked against the
exact public build before release.
