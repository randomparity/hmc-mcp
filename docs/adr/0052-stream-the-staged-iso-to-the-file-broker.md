# 0052 — `hmc_upload_iso` streams the staged ISO to the file broker

## Status

Accepted (2026-08-20)

## Context

`upload_iso` downloads the caller's ISO to a temp file with care: 8 KiB chunks,
a running SHA-256, and the size bound checked inside the loop
(`operations_storage.py:374-385`). Nothing is held in memory. It then read the
file back in one call and handed the result to the broker:

```python
with iso_path.open("rb") as f:
    content = f.read()
await hmc._broker_file_upload(broker_uri, content)
```

The only size gate on that read is the one the download already applied:
`MAX_DOWNLOAD_SIZE_BYTES = 100 * 1024 * 1024 * 1024` (`operations_storage.py:29`)
— 100 GiB. Read from source at `61c3026`: no second check exists anywhere
between the download call and the read. So any file that passed the download
bound became a single allocation of its own size. A 20 GiB AIX or Linux install
image is well inside the permitted range, ordinary, and a 20 GiB allocation.

The shape is worse than the number. The streaming above it *looks* like the
operation is bounded, and one line undoes it. A reader auditing the download
loop finds a careful bound and stops there.

The process is shared: one MCP server serves every caller of every tool, so an
allocation large enough to reach the OOM killer or drive the host into swap is a
denial of service against all of them. Reaching it needs a `mutate` grant naming
a real VIOS (`hmc_upload_iso` is `effect="mutate"`, `target_kind="vios"`), so
this is an authorized-caller fault, not an anonymous one — and it is as easily
reached by an operator uploading a genuinely large ISO as on purpose. That is
#308.

## Decision

**The staged file is streamed to the broker. Nothing reads it whole.**

`operations_storage._aiter_file_chunks(handle, chunk_size=DEFAULT_CHUNK_SIZE)`
is an async generator yielding the handle's bytes in chunks, and `upload_iso`
passes it — not `bytes` — to the broker upload, under the length the download
already returned:

```python
with iso_path.open("rb") as f:
    await hmc._broker_file_upload(broker_uri, _aiter_file_chunks(f), file_size)
```

`HMCClient._broker_file_upload` changes signature accordingly, from
`(broker_uri, content: bytes)` to
`(broker_uri, content: AsyncIterator[bytes], content_length: int)`. It is
private, has exactly one caller, and reaches no public API, MCP tool, or CLI
command — the ADR 0031 amendment records the change there rather than leaving
that ADR describing a signature that no longer exists.

Three properties are transport constraints, not style, each verified against the
installed httpx 0.28.1 rather than taken from documentation:

**The iterator must be async.** A file object or a sync generator is encoded as
an `IteratorByteStream`, which is a `SyncByteStream`, and
`AsyncClient._send_single_request` raises `RuntimeError: Attempted to send an
sync request with an AsyncClient instance`. *Verified* by probe: both forms
raise; an async generator sends. The obvious implementation — hand the broker
the open file — does not work, which is worth recording because it will be the
first thing a future reader tries.

**`content_length` is passed explicitly, and there is no second pass over the
file to compute it.** `_download_iso_from_url` already returns the byte count it
counted while hashing. Without an explicit `Content-Length`, httpx's
`encode_content` sets `Transfer-Encoding: chunked` for an iterator body;
`Request._prepare` skips that when the header is already present. *Verified* by
probe: with the header, `Content-Length: 6` and no `Transfer-Encoding`; without
it, `Transfer-Encoding: chunked` and no length. ADR 0031 records that the
brokered upload requires `Content-Length`, so the header is load-bearing.

**The body is consumed exactly once and is never replayed.** This is the risk
streaming introduces, and it is worth stating at its true size rather than its
scariest one. Two guards sit under it, both *verified* here:

- Re-sending the **same** `Request` raises `httpx.StreamConsumed`
  (`httpx/_content.py:75-77` — `AsyncIteratorByteStream.__aiter__` raises when
  the stream was already consumed and wraps an async generator).
- Building a **new** `Request` around the exhausted generator does *not* raise:
  the new stream object iterates an exhausted generator and yields nothing, so
  the body is empty under the `Content-Length` that was passed in — reproduced
  by probe. But h11 refuses to send it: against a real socket, a body shorter
  than the declared length raises
  `LocalProtocolError: Too little data for declared Content-Length`. Reproduced
  by probe on a loopback server.

So a truncated ISO cannot reach the HMC *quietly* — the request fails loudly at
the transport instead. What remains is that the failure would be confusing and
the operation would still be wrong, and that the guarantee rests on the length
and the body coming from the same source. That is enough reason to keep
single-send as a design constraint rather than adding an executable guard whose
job h11 already does. Nothing in this path can retry, and each of these was read
from source rather than assumed:

- `HMCClient._request` (`client.py`) sends once, and its `except` arms only
  translate `httpx.TimeoutException` / `TransportError` into `HMCTransportError`
  and re-raise. No loop, no re-send.
- `httpx.AsyncClient` is constructed without `follow_redirects`
  (`client.py:142-151`), and the httpx default is `False` (*verified* by probe),
  so a 3xx is returned to the caller rather than re-sent to a new target.
- The default transport's connection retries are `0` (*verified* by probe), and
  httpx's `retries` covers connection establishment, not a request already sent.
- There is no 401-triggered re-logon-and-retry anywhere in the client; the
  session is established once in `__aenter__`.
- `upload_iso` calls `_broker_file_upload` once, in a `try` whose `finally`
  cleans up — it does not retry the upload.

The `_broker_file_upload` docstring carries this, with the instruction that any
future retry, redirect-following, or shared client must make the body
re-creatable (a factory per attempt) in the same change.

**The handle is opened by `upload_iso`, not by the generator.** The `with` block
spans the upload, so the descriptor is released deterministically on every
outcome rather than whenever the generator is finalized. That is a descriptor
leak this guards, not an unlink: on POSIX an open handle does not prevent
`unlink`, so `assert not staged.exists()` cannot see a leaked one. The test that
holds the opened handle and asserts `handle.closed` is what pins it — with the
`with` replaced by a bare `open()`, every other assertion in the file stays
green.

## Consequences

**Memory used by the upload no longer scales with the ISO.** It is one chunk at
a time, `UPLOAD_CHUNK_SIZE` (64 KiB).

**The upload gets its own chunk constant rather than reusing the download's.**
Reusing `DEFAULT_CHUNK_SIZE` (8 KiB) looked like avoiding a second knob, and
that reasoning was wrong: the two constants measure different things. The
download's is a read hint over an already-buffered response, while the upload's
is a *wire* unit — httpcore issues one `network_stream.write` per chunk an async
iterator yields (`httpcore/_async/http11.py:157-166`), where a `bytes` body
produced one write for the whole payload. At 8 KiB a 20 GiB ISO is 2.6 million
writes. Measured on the development host over loopback, a 256 MiB PUT ran at
431 MB/s with 8 KiB chunks and 2428 MB/s with 64 KiB — loopback exaggerates it,
since a real upload is link-bound, but the per-write overhead is real and the
fix costs nothing. 64 KiB is httpx's own streaming unit
(`AsyncIteratorByteStream.CHUNK_SIZE`). Memory stays bounded either way.

**`MAX_DOWNLOAD_SIZE_BYTES` is unchanged at 100 GiB, deliberately.** It is an
operator-visible bound and changing it is a separate decision (see below). What
this ADR removes is the claim that the bound was ever sized for a resident
allocation. It bounds what the temp filesystem absorbs, which is what it should
have bounded all along.

**The shape now matches what the code does.** Download streams, upload streams;
there is no line in the middle that undoes the one above it.

**Unchanged, and now pinned by test:** broker cleanup on every outcome and the
media-name collision refusal.

**One thing was not unchanged, and an earlier draft of this ADR said it was.**
Temp-file removal did *not* happen on every failure path: the download landed
before the collision check, but the `try` whose `finally` unlinks began after
it, so a `media_name` that already existed — or any failure reading the
repository — returned an error with the whole staged download still on disk, up
to the 100 GiB bound and repeatable at will. The leak predates this change; the
false claim did not. The cleanup arm now opens immediately after the download,
and three tests pin it: the collision path, a failing repository read, and the
descriptor the upload streams from.

**Still open, filed separately:** the media-name collision check still runs
*after* the download, so a large fetch precedes a refusal that could have come
first (#325). Moving the check would close that; this change only stops the
fetch being *stranded*, and leaves the ordering to #325.

**The proof is a shape test, not a memory measurement.** `_broker_file_upload`'s
body is asserted to leave the process as an async-only stream carrying the
caller's chunks separately, under a `Content-Length` equal to the bytes actually
sent — assertions that require a transport of the test's own, because respx and
`httpx.MockTransport` both call `request.aread()` before a route sees the
request, which materializes the stream and makes `request.content` answer
identically for a streamed and a slurped body. A test written only against respx
cannot tell the two apart, which is why one of these tests does not use it.

## Considered & rejected

Grounds are separated by what was checked and what was judged, because an
unverified rejection can stand unchallenged for years.

**Lowering `MAX_DOWNLOAD_SIZE_BYTES` instead of streaming.**

Rejected. This is the alternative #308 itself raised, and the smaller change by
a wide margin: one constant, no signature change, no ADR amendment.

*Verified:* it does not remove the defect, it resizes it. The read is still a
whole-file read, so the resident allocation is still exactly whatever the bound
permits — the operation's peak memory remains an operator-configurable number
rather than a constant. And the bound is a *download* bound: it is enforced
while writing to disk, and the read that consumes the file re-derives its safety
from it. Nothing in the code says the two are coupled, so a later change to the
download bound silently moves the upload's memory ceiling.

*Verified:* it also leaves the stream-then-slurp shape in place — the specific
thing that made this defect survive review, where careful chunked streaming is
followed by a single `f.read()`. A lower number makes the misleading shape
cheaper, not clearer.

*Design judgement:* there is no defensible number. Too low and ordinary install
images are refused, which turns into pressure to raise it again; high enough for
a real DVD-sized image and the allocation is still large in a shared process.
The right value depends on the host's memory, which the code does not know. A
bound that has to be tuned per deployment to avoid an OOM is not a fix.

*Judgement, and deliberately not acted on here:* 100 GiB is arguably too large
for what a *disk* bound should permit either — it is roughly two orders of
magnitude above any real install image. But that is an operator-visible refusal
threshold, and changing it changes which uploads succeed. It belongs to whoever
owns that decision, not to this change, which is why the constant is untouched.

**Passing the open file object, or a sync generator, to the broker upload.**

Rejected. *Verified:* both raise `RuntimeError` at send time under httpx 0.28.1
(probe above). This is not a preference — the implementations do not work.

**Adding a dependency that offers async file I/O (e.g. `aiofiles`).**

Rejected. *Verified:* nothing needs it — an async generator over a plain
`handle.read()` satisfies httpx's requirement, which is about the *iterator*
being async, not about the read being non-blocking. *Design judgement:* the
chunk reads are the same synchronous reads the download loop already performs
while writing (`f.write(chunk)` in an async loop), so a dependency would buy
consistency with nothing and add supply-chain surface to a private primitive.

**Having `_broker_file_upload` take the path and open the file itself.**

Rejected. *Design judgement:* `HMCClient` is the transport seam and knows about
requests, not staged temp files; ADR 0031 defines these primitives at that
boundary. Opening the file inside the client would also move the handle's
lifetime into the client, where the `finally` arm that unlinks the temp file
cannot see it — the failure mode being an unlink blocked by a handle nobody
closed.
