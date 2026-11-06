# Source fetching

Read the method relevant to the source in hand.

## Chase these pointers

| Content type | How to detect | How to fetch |
|---|---|---|
| Article / blog / newsletter | any web URL | fetch the page text; paywalled posts give the free portion only (say so) |
| Video / podcast | YouTube, podcast hosts, native video | transcript first (platform captions, a transcript API, `yt-dlp --write-auto-sub --skip-download`); otherwise download the audio and see [Transcription](transcription.md) |
| Social thread | same conversation id + same author | collect the whole thread, not one post; compile it as one source |
| Quoted / parent posts | a quote or reply reference | fetch the quoted item too: it often carries the actual link |
| Long-form posts on social platforms | an "article" field or a truncated body | API fields often return only a preview; if the body is under a few hundred characters, read it in a logged-in browser |
| Images / screenshots | media attachments | read the text in them and transcribe the numbers into the raw file (an image is invisible to search) |
| Public chat channels | a channel URL with a public web view | fetch the web view; paginate if needed |

## Cannot fetch

Some platforms need the human to paste the content (private or login-walled apps). Say which, and save what was pasted under the real author.

## Provenance header (top of every raw file)

```
Source: <url>
Author: <name> (<handle>)
Published: <original timestamp, not the fetch time>
Fetched: <YYYY-MM-DD> via <method>
Speakers: <multi-speaker transcripts: who is who; mark ambiguous passages "shared">
```
