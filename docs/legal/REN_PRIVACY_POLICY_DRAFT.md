# Ren Privacy Policy

> **DRAFT FOR LEGAL REVIEW - NOT AN APPROVED OR ACTIVE PRIVACY NOTICE.**
> This draft describes the current Ren code reviewed on October 4, 2026.
> Confirm it against the release build, its bundled dependencies, and the
> final support process before publication.

Proposed operator: **[LEGAL ENTITY AND PRIVACY CONTACT TO BE COMPLETED]**

Draft date: October 4, 2026

## Questions for counsel before approval

1. Identify the entity responsible for Ren, its contact details, and whether
   it acts as a controller, processor, service provider, or another role in
   each supported market and use case.
2. Which jurisdictions and customer types are in scope? Determine the
   required legal bases, notices, rights, response deadlines, retention
   rules, cross-border terms, and request channels for each.
3. Is the public edition limited to adults or business users? What notices,
   age handling, and consent are required if footage includes children or
   other sensitive personal information?
4. Which model and agent providers will be documented as subprocessors or
   independent providers? Confirm their data use, retention, training,
   security, and transfer terms, and whether Ren's users need additional
   notice before invoking them.
5. Define retention and deletion practices for local diagnostics that users
   voluntarily send to support, including any support bundle format and
   support ticket system.
6. Confirm whether any bundled or downloaded component sends diagnostics,
   usage information, crash reports, or other data independently of Ren.
   This draft describes Ren's first-party code and must not imply control over
   third-party software or the user's agent harness.
7. Review this notice against applicable privacy laws and the exact public
   release. The [FTC's app guidance](https://www.ftc.gov/business-guidance/resources/marketing-your-mobile-app-get-it-right-start)
   advises businesses to make privacy statements clear and accurate and to
   honor the practices they describe. Review the [California Attorney
   General's CCPA guidance](https://oag.ca.gov/privacy/ccpa) and other
   jurisdiction-specific requirements where applicable.

## 1. What this policy covers

This draft covers the public edition of Ren's locally installed video
editing software. It does not cover the separate services you choose to use
with Ren, including your agent harness and model provider, DaVinci Resolve,
websites, package registries, or third-party components. Those providers
control their own services and policies. See
[`THIRD_PARTY_NOTICES`](../../THIRD_PARTY_NOTICES) for Ren's current
component inventory.

## 2. Privacy at a glance

Ren's first-party code runs on your device and has no Ren account service,
first-party analytics, advertising, or usage telemetry upload. Ren does not
send project media, prompts, run logs, or support archives to Ren's operator
in the background.

Some features can pass model requests to the agent harness you selected.
That is a separate provider interaction: Ren prepares a request locally, and
the harness may send the prompt and information you asked it to inspect to
its configured model provider. Some image-inspection paths pass extracted
still images to the installed Codex CLI. The provider's terms and settings
govern that processing. Review them before using those features.

Ren and its optional capabilities may also download software packages or
model files from their configured upstream sources. A download request can
expose ordinary connection information, such as your IP address, to that
source. A gated download may send the access token you configured to its
provider. These are downloads initiated for setup or a feature, not Ren
telemetry.

## 3. Information Ren processes locally

Depending on the features you use, Ren can read or create the following on
your device:

- Project files, settings, footage paths, video and audio media, images,
  captions, transcripts, timeline data, and exported media.
- Media-derived information such as recognized speech, clip descriptions,
  image observations, face or subject measurements, timing, and edit plans.
- Prompts, selected project context, agent responses, run status, timing
  measurements, and diagnostic logs.
- Configuration and local cache files, including downloaded model weights.

Ren writes pipeline artifacts in the project, including local model request
and response files. A request file can include its prompt, constraints,
rendered context, expected output schema, project path, step identifier, and
timestamp. The prompt or context may contain text or observations derived
from your media. Local files may therefore contain sensitive information.
They remain on your device unless you or another program sends or shares
them.

Local Gemma inference runs in the Ren process or through the local Gemma
server. The default server address is loopback on your own device. If you
change the server URL or configure another endpoint, that endpoint may
receive the prompt and images sent to it.

## 4. Information sent to services you choose

### Agent harness and model provider

For model-assisted steps, Ren writes a request file under the project's
`pipeline_output/llm_requests/` directory and waits for a response under
`pipeline_output/llm_responses/`. The request contains the step prompt,
constraints, selected context, expected output schema, project path, and
timestamp. The selected host reads that request and answers it. Ren's normal
agent flow uses the harness rather than a Ren-operated provider API.

What the provider receives depends on your host, its tools, its configuration,
and the task. It may receive prompt text and project-derived context. Some
requests refer to local frame strips for the host to inspect; still-image
inspection can invoke the Codex CLI with extracted still images. The harness
may send those images or other content to its provider. Ren does not control
the provider's collection, retention, model-training, or deletion practices.
Check the host and provider terms and settings. The local request and
response files may remain in your project after the model call.

### Downloads and user-directed retrieval

Setup and optional features may fetch packages, model weights, or helper
assets from upstream sources such as package registries, model repositories,
or source-hosting services. Those sources receive standard request metadata
and any credentials you configure for gated downloads. If you ask Ren or a
third-party tool to retrieve a URL or remote media, the remote host receives
the request needed to provide that material.

Ren does not upload your project footage as part of ordinary dependency or
model downloads. This statement does not cover uploads performed by your
chosen agent harness, remote endpoint, Resolve, or another third-party tool.

### Diagnostics and support

Ren does not automatically upload logs or support bundles. The current code
has no built-in support-bundle uploader. If you choose to create or send a
support bundle or other diagnostic files, you decide what to share and with
whom. The recipient receives the contents you send. Review files before
sharing because they may include project paths, prompts, context, logs, or
other sensitive details.

## 5. Telemetry and analytics

Ren's first-party code does not send usage analytics, advertising events,
crash reports, or performance telemetry to Ren's operator. Ren may record
local run and performance measurements to files on your device so you can
inspect what happened. This statement does not cover data independently
collected by your operating system, agent harness, model provider, Resolve,
or another third-party component.

## 6. Retention and deletion

Project files, request and response records, logs, caches, and downloaded
models are stored locally under locations chosen by the installation and
project configuration. They remain until you or software you control removes
them. Ren cannot remotely delete local files. Your agent or model provider
may retain submitted content under its own policy and account settings.

If you voluntarily send information to support, the approved retention
period and deletion contact must be completed here: **[SUPPORT RETENTION AND
CONTACT TO BE COMPLETED]**.

## 7. Security

Ren processes local files and may write sensitive project-derived content to
local artifacts and logs. Protect your device, project folders, backups, and
agent credentials. Before sharing diagnostics, inspect their contents. The
final policy should describe only security measures that the release team
can verify and maintain.

## 8. Your choices and privacy requests

You choose whether to run model-assisted features and which supported
harness or account to use. You can avoid sending a request to a model
provider by not using a feature that invokes that provider. Local files can
be reviewed and removed using your normal file-management tools, subject to
your backups and other copies.

The process for exercising privacy rights depends on your location and on
whether the relevant information is held by Ren, locally by you, or by a
third-party provider. The final notice must provide the approved contact and
request process: **[PRIVACY CONTACT AND RIGHTS PROCESS TO BE COMPLETED]**.

## 9. Changes and contact

The approved policy should state how Ren will announce material changes and
when a revised policy takes effect. Contact: **[PRIVACY CONTACT TO BE
COMPLETED]**. Until the open items above are resolved and the public notice
is approved, this document remains a draft and must not be treated as the
operative privacy policy.
