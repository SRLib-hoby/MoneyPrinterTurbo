import { spawnSync } from 'node:child_process';
for (const name of ['APP_ACCESS_PASSWORD', 'DEEPSEEK_API_KEY', 'MINIMAX_API_KEY', 'R2_ENDPOINT_URL', 'R2_BUCKET_NAME', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY']) {
  console.log(`Set ${name}${name === 'APP_ACCESS_PASSWORD' ? ' (at least 24 characters)' : ''}:`);
  const result = spawnSync(process.execPath, ['node_modules/wrangler/bin/wrangler.js', 'secret', 'put', name], { stdio: 'inherit' });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
