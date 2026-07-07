# Prompting Nano Banana

General guidance for writing prompts to Gemini image models. Project- or asset-specific instructions in sibling files override this.

## Core approach

Describe the scene, don't list keywords. Nano Banana responds best to narrative, specific prose — "a photorealistic close-up of a weathered fisherman's hands tying a fly, golden hour light through a rain-flecked window" beats "fisherman, hands, fly, golden hour, photorealistic, 8k".

Include, when relevant:

- **Medium/style**: photo, watercolor, flat vector, oil painting, pixel art, isometric render
- **Composition**: close-up, wide shot, top-down, centered on white background
- **Lighting**: golden hour, soft studio light, dramatic rim lighting, overcast
- **Intent for assets**: "flat vector icon, single subject, plain white background, no text" for icons; "seamless tileable texture" for textures

## Text in images

Nano Banana renders text fairly well but spell it explicitly and keep it short: `the sign reads "OPEN"`. Verify rendered text by reading the output image — regenerate if garbled.

## Aspect ratio and size

There is no size parameter on the generateContent path — state it in the prompt ("wide 16:9 banner", "square icon", "tall 9:16 poster") and the model complies.

## Reference images

- `--style-ref` transfers *look* (palette, technique, mood) while ignoring the reference's subjects.
- `--ref` holds a *subject's identity* (a character, a product) across generations. Repeatable.
- The prompt should name references positionally: images arrive as text → style ref → subject refs, so "in the style of the first image, the character from the second image standing on a cliff".

## Editing an existing image

Pass the image as `--ref` and describe the change: "the same scene as the first image, but at night with lit windows". For surgical edits, be explicit about what to keep: "change only the background; keep the character identical".
