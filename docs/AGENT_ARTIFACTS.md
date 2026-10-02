# Agent visual artifacts

The agent progressively reads the bundled `artifacts` skill through `read_skill`.
The dispatcher enforces reading it in the current request before `create_artifact`;
the system prompt carries only a short capability pointer. Skill resources are
included in wheels, rather than referencing an unpackaged checkout directory.

`create_artifact` validates bounded static HTML/CSS and asks the private renderer
for up to three 800px-wide pages at 2x density. It checks returned PNGs before
persisting a UUID, source, images, creator, chat, forum topic and seven-day expiry.
Creation has no Telegram side effect. Source edits produce a new immutable ID;
`get_artifact` provides the original HTML/CSS for known IDs in the same chat/topic.
A request has at most three creation attempts. A chat retains at most 30 live
artifacts; expired rows are purged on subsequent creation.

`send_artifact` has no destination argument. Every lookup includes trusted chat
AND topic values from the inbound message, plus expiry, even when an attacker
knows a real UUID from another chat. Unauthorized and absent IDs have the same
error. Existing agent access checks also apply; artifact sending grants no new
moderation permissions. Stored source is untrusted data, never authorization.

Delivery uses photos only, not documents. Captions of at most 1024 UTF-16 units
after Markdown conversion accompany the first photo. Longer text follows all
photos in balanced HTML chunks of at most 3500 units. Multi-page images are sent
as sequential photos (at most three) so delivery can be resumed per message.
Telegram entity rejections fall back to plain text; other errors aren't hidden.
Confirmed message IDs/progress are persisted. Retry never repeats acknowledged
messages. A pending reservation before each send survives a crash/timeout:
uncertain delivery refuses automatic replay because Telegram may have accepted
it. This conservatively favors avoiding duplicate messages over silent retries.
A completely delivered ID is idempotent; create a new ID for another delivery.

## Deployment

Sync the updated docker-compose.yml to the VPS before the next manual deploy.
The existing app image is also used for `artifact-renderer`, with a different
entrypoint. Publish Docker Image already publishes this image. Deploy To VPS
pulls/starts the renderer together with app and web. No deploy is automatic.

Renderer has NO env_file, bot tokens, database volumes, host ports or access to
the database network. It runs as UID 10001 with read-only rootfs, tmpfs, dropped
capabilities, no-new-privileges, CPU/memory/PID limits and an internal-only network
shared solely with app. Browser binaries are installed in /ms-playwright and are
readable by the worker. The app's ARTIFACT_RENDERER_URL defaults to
http://artifact-renderer:8090; override only with a trusted private endpoint.

Chromium is isolated by the container (it uses --no-sandbox inside it). Static
markup is allowlisted; JavaScript, service workers, external requests/resources,
iframes, event handlers and active SVG are disabled/rejected. CSP and request
interception supplement the container's network boundary. Local controlled DOM
measurement checks overflow, clipped text, font size and page size. No tools
execute model Python, shell or JavaScript. Render timeout is 20s, client timeout
25s, input is bounded to 256KB, each PNG to 2MB. A renderer outage preserves the
text response path and cannot accidentally send an unverified artifact.

Automatic checks do not prove factual accuracy or aesthetic quality. The current
API returns technical diagnostics rather than a vision preview. Skills require
honest descriptions and no claims of having inspected the screenshot. JPEG/
photo transformations by Telegram also preclude a promise of lossless delivery.
