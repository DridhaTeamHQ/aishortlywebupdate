# Web Compatibility Wrapper

This folder exists for deployment compatibility.

- Recommended Vercel project roots: repository root or `frontend`
- Compatibility root: `web`

If a Vercel project is still configured with `web` as the root directory, this wrapper forwards install, build, start, and lint commands to the real Next.js app in `frontend/`.

When using `web` as the Vercel root, keep `Include files outside the root directory in the Build Step` enabled so the wrapper can access `../frontend`.
