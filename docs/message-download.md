# Message Download (DOCX export) — Frontend Tutorial

Adds a "Download" button on an assistant message that saves the question +
answer as a `.docx` file. This backend endpoint already exists; this doc
walks through wiring it into `client/frontend-nuxt`, following the same
pattern already used for the "Share" button (`useShare.ts` /
`server/api/share.post.ts`).

## 0. The backend contract

```
GET /api/v2/history/messages/{message_id}/download?format=docx
Authorization: Bearer <access_token>
```

- `message_id` — a persisted `msg-*` id (`message.messageId` in the chat store).
- `format` — optional, defaults to `docx`. It's the only supported value right
  now; the param is reserved so `format=pdf` can be added later without a
  breaking change.
- Success: `200`, body is raw `.docx` bytes, with
  `Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document`
  and `Content-Disposition: attachment; filename="<message_id>.docx"`.
- Errors: `400` (bad `format`), `403` (message belongs to another user),
  `404` (no such message).

The frontend never needs to know the user's id for this call — ownership is
checked against the JWT the Nuxt server signs, same as every other
`history/*` route.

The doc renders `content.query` (italicized) followed by `content.response`
converted from markdown into real Word formatting (headings, bold/italic,
lists, tables, blockquotes render natively, not as literal `**`/`#`/`|`).

## 1. Add a Nuxt server proxy route

The browser never calls the WakilAI API directly — everything goes through
a Nuxt server route that signs the backend JWT
(`createAuthenticatedBackendHeaders`) from the user's session. This route
needs the *raw* bytes and the `Content-Disposition` header from the backend
response, so it uses `fetch` directly instead of the `backendFetch` JSON
helper (same reason `server/api/speech.post.ts` does its own `fetch` for
multipart).

Create `server/api/history/messages/[message_id]/download.get.ts`:

```ts
import { createAuthenticatedBackendHeaders, getBackendRuntimeConfig } from '../../../../utils/backend/config'

export default defineEventHandler(async (event) => {
  const session = await getUserSession(event)
  if (!session?.user) {
    throw createError({ statusCode: 401, statusMessage: 'Unauthorized' })
  }

  const { message_id } = event.context.params as { message_id: string }
  const format = String(getQuery(event).format ?? 'docx')

  const { apiBaseUrl } = getBackendRuntimeConfig()
  const headers = await createAuthenticatedBackendHeaders(event, {
    accept: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  })

  const backendUrl = `${apiBaseUrl}/api/v2/history/messages/${message_id}/download?format=${encodeURIComponent(format)}`
  const response = await fetch(backendUrl, { headers })

  if (!response.ok) {
    const detail = await response.json().catch(() => null) as { detail?: string } | null
    throw createError({
      statusCode: response.status,
      statusMessage: detail?.detail || 'Failed to download message'
    })
  }

  setResponseHeader(event, 'Content-Type', response.headers.get('content-type') ?? 'application/octet-stream')
  setResponseHeader(
    event,
    'Content-Disposition',
    response.headers.get('content-disposition') ?? `attachment; filename="${message_id}.${format}"`
  )

  return Buffer.from(await response.arrayBuffer())
})
```

`../../../../utils/backend/config` is 4 levels up from
`server/api/history/messages/[message_id]/` — one more than the existing
`[session_id].get.ts` sibling since this route adds a nested `[message_id]/`
folder. Adjust if you flatten it to `download-[message_id].get.ts` instead
(then it'd be `../../../utils/backend/config`, matching the sibling file).

## 2. Add a composable

Mirrors `app/composables/useShare.ts`. Create
`app/composables/useMessageDownload.ts`:

```ts
import { ref } from 'vue'
import { isBackendMessageId } from '~/features/chat/message'

export function useMessageDownload() {
  const downloading = ref(false)

  async function downloadMessage(messageId: string, format: 'docx' = 'docx') {
    const backendMessageId = messageId.trim()
    if (!isBackendMessageId(backendMessageId)) {
      throw new Error('A persisted backend message ID is required to download a message.')
    }

    downloading.value = true
    try {
      const response = await $fetch.raw(
        `/api/history/messages/${backendMessageId}/download`,
        { query: { format }, responseType: 'blob' }
      )

      const blob = response._data as Blob
      const filename = response.headers.get('content-disposition')
        ?.match(/filename="?([^"]+)"?/)?.[1] ?? `${backendMessageId}.${format}`

      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      link.click()
      URL.revokeObjectURL(url)
    } finally {
      downloading.value = false
    }
  }

  return { downloading, downloadMessage }
}
```

## 3. Wire up the button

In `app/pages/chat/[id].vue`, next to the existing share button (~line 693):

```ts
import { useMessageDownload } from '~/composables/useMessageDownload'
// ...
const { downloading, downloadMessage } = useMessageDownload()

async function handleDownload(message: ChatMessage) {
  const messageId = message.messageId
  if (!canShareMessage(message) || !messageId) return

  try {
    await downloadMessage(messageId)
  } catch {
    toast.add({
      description: conversationMessages.value.download.error,
      icon: 'i-lucide-alert-circle',
      color: 'error'
    })
  }
}
```

```vue
<UButton
  v-if="msg.content && !loading && canShareMessage(msg)"
  icon="i-lucide-download"
  color="neutral"
  variant="ghost"
  size="xs"
  :disabled="downloading"
  class="opacity-30 group-hover/msg:opacity-100 transition-opacity duration-150"
  :aria-label="conversationMessages.download.download"
  @click="handleDownload(msg)"
/>
```

`canShareMessage` also gates on `message.shareReady`, a flag specific to the
share-link feature. If download should be available before that flag flips
(e.g. as soon as the message is persisted), add a small sibling in
`app/features/chat/message.ts` instead of reusing `canShareMessage`:

```ts
export function canDownloadMessage(message: ChatMessage): boolean {
  return message.role === 'assistant'
    && Boolean(message.content)
    && isBackendMessageId(message.messageId)
}
```

## 4. Add i18n strings

In `app/lib/i18n/en.ts`, `ru.ts`, and `uz.ts`, next to the existing `share:`
block (~line 550 in `en.ts`):

```ts
download: {
  download: 'Download as Word',
  error: 'Failed to download message'
},
```

(Translate the two strings for `ru.ts` / `uz.ts`.)

## 5. Test it

- Ask a question that produces a rich answer (headings, a list, maybe a
  table) so you can confirm formatting survives.
- Click Download, confirm a `.docx` downloads and opens correctly in Word /
  Google Docs / LibreOffice, with the question italicized above a divider
  and the answer properly formatted below (not raw `**`/`#` markdown).
- Try it on a message that isn't yet persisted (no `messageId`) — the button
  should not render (`canShareMessage`/`canDownloadMessage` gate handles
  this).
- Confirm a 403 (e.g. querying another user's `message_id` directly) surfaces
  the error toast instead of silently failing.

## Formats

Only `docx` works today. `format=pdf` will 400 until PDF export ships on the
backend — the query param is already there so this whole flow won't need to
change when it does; only the button will multiply into a small menu
("Download as Word" / "Download as PDF").
