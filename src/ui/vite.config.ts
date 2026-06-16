import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
  // This project lives under a OneDrive-synced folder on Windows. OneDrive (and
  // AV) hold locks on node_modules/.vite while Vite tries to remove its
  // dependency-cache temp dirs during re-optimization, causing
  // "EPERM: operation not permitted, rmdir ...". Keeping the cache outside the
  // synced tree avoids the contention.
  cacheDir: join(tmpdir(), 'vite-project-analyzer'),
});
