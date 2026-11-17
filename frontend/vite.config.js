/* global process */
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `npm run dev:https` serves the app over https (needed for the live camera on
// phones) using the self-signed certificate from `npm run make-cert`.
const certDir = fileURLToPath(new URL('./.certs/', import.meta.url))
const keyFile = `${certDir}dev-key.pem`
const certFile = `${certDir}dev-cert.pem`
const useHttps = process.env.VITE_DEV_HTTPS === '1'

if (useHttps && !(fs.existsSync(keyFile) && fs.existsSync(certFile))) {
  throw new Error('No dev certificate found. Run `npm run make-cert` first.')
}

// A page on https can't call a plain-http API (browsers block mixed content),
// so in https mode the app calls /api on its own origin and Vite forwards it.
// xfwd adds the real client address for the backend's network check (run the
// backend with TRUST_PROXY_HOPS=1).
const apiTarget = process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:5001'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    https: useHttps
      ? { key: fs.readFileSync(keyFile), cert: fs.readFileSync(certFile) }
      : undefined,
    proxy: useHttps
      ? {
          '/api': {
            target: apiTarget,
            xfwd: true,
            rewrite: (path) => path.replace(/^\/api/, ''),
          },
        }
      : undefined,
  },
})
