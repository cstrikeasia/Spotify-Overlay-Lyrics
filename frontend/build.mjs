/** Build offline OBS assets with Tailwind CSS 4, no Vite / CDN / fetch.
 * Vue's global browser runtime compiles the small HTML Vue template on mount.
 * The outputs are prebuilt and shipped in Python/EXE so users need no Node. */
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const read = p => fs.readFileSync(p, 'utf8');
const sha = bytes => createHash('sha256').update(bytes).digest('hex').slice(0, 12);
const pathTo = (...parts) => path.join(here, ...parts);
const tailwindRoot = process.env.TAILWIND_PACKAGE_ROOT || path.dirname(require.resolve('tailwindcss/package.json'));
const { compile } = require(path.join(tailwindRoot, 'dist/lib.js'));

const html = read(pathTo('index.html'));
const sourceFiles = [
  'src/core.js',
  'src/controllers/progress.js',
  'src/controllers/artwork.js',
  'src/controllers/lyrics.js',
  'src/controllers/stream.js',
  'src/controllers/layout.js',
  'src/controllers/motion.js',
  'src/app.js',
  'src/main.js',
];
const script = [
  '(() => {',
  "'use strict';",
  ...sourceFiles.map(filename => read(pathTo(filename))),
  '})();',
].join('\n\n');
const style = read(pathTo('src/styles.css'));
const preflight = read(path.join(tailwindRoot, 'preflight.css'));
const theme = read(path.join(tailwindRoot, 'theme.css'));

// V4's compiler is invoked directly: it generates only the classes used by
// the Vue template, rather than shipping a 2MB runtime Tailwind CDN build.
const input = [
  '@layer theme, base, components, utilities;',
  `@layer theme { ${theme} }`,
  `@layer base { ${preflight} }`,
  '@tailwind utilities;',
  style,
].join('\n');
const compiler = await compile(input);
const templateClasses = [...html.matchAll(/(?<!:)class="([^"]+)"/g)]
  .flatMap(match => match[1].split(/\s+/g))
  .filter(Boolean);
const outputCss = compiler.build([...new Set(templateClasses)]);
const vueRuntime = fs.existsSync(pathTo('node_modules/vue/dist/vue.global.prod.js'))
  ? fs.readFileSync(pathTo('node_modules/vue/dist/vue.global.prod.js'))
  : fs.readFileSync(pathTo('vendor/vue.global.prod.js'));

const dist = pathTo('dist');
fs.rmSync(dist, { recursive: true, force: true });
fs.mkdirSync(path.join(dist, 'assets'), { recursive: true });
const writeAsset = (name, content) => {
  const bytes = Buffer.isBuffer(content) ? content : Buffer.from(content, 'utf8');
  const filename = `${name}-${sha(bytes)}${name === 'overlay-css' ? '.css' : '.js'}`;
  fs.writeFileSync(path.join(dist, 'assets', filename), bytes);
  return `/assets/${filename}`;
};
const cssUrl = writeAsset('overlay-css', outputCss);
const vueUrl = writeAsset('vue3', vueRuntime);
const jsUrl = writeAsset('overlay-js', script);
fs.writeFileSync(path.join(dist, 'index.html'), html
  .replace('__OVERLAY_CSS__', cssUrl)
  .replace('__VUE_JS__', vueUrl)
  .replace('__OVERLAY_JS__', jsUrl), 'utf8');
console.log(`OBS Vue+Tailwind build: ${cssUrl}, ${jsUrl}, ${vueUrl}`);
