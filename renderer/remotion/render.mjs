import fs from 'node:fs/promises';
import {writeFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition} from '@remotion/renderer';

const root = path.dirname(fileURLToPath(import.meta.url));
const job = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const bundlePath = job.bundlePath;
const inputProps = JSON.parse(await fs.readFile(job.propsPath, 'utf8'));
const browserExecutable = process.env.OLYPHANTS_CHROME_PATH || undefined;
const chromiumOptions = {gl: 'swangle', enableMultiProcessOnLinux: true};
let lastProgress = -1;

try {
  const serveUrl = await bundle({
    entryPoint: path.join(root, 'src/index.tsx'),
    outDir: bundlePath,
    rootDir: root,
    publicDir: path.join(root, 'public'),
    enableCaching: true,
    webpackOverride: (config) => config,
  });
  const composition = await selectComposition({
    serveUrl,
    id: 'OlyphantsVideo',
    inputProps,
    browserExecutable,
    chromiumOptions,
    logLevel: 'error',
    dumpBrowserLogs: true,
  });
  await renderMedia({
    composition,
    serveUrl,
    inputProps,
    outputLocation: job.outputPath,
    codec: 'h264',
    pixelFormat: 'yuv420p',
    crf: 20,
    concurrency: 1,
    browserExecutable,
    chromiumOptions,
    logLevel: 'error',
    dumpBrowserLogs: true,
    isProduction: true,
    onProgress: ({progress}) => {
      const percent = Math.max(0, Math.min(100, Math.round(progress * 100)));
      if (percent !== lastProgress) {
        lastProgress = percent;
        writeFileSync(job.progressPath, JSON.stringify({progress: percent}));
      }
    },
  });
  process.stdout.write(JSON.stringify({status: 'RENDERED', outputPath: job.outputPath}) + '\n');
} catch (error) {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exitCode = 1;
} finally {
  await fs.rm(bundlePath, {recursive: true, force: true});
}
