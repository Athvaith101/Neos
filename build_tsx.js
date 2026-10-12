// Compiles web/src/*.tsx -> web/assets/*.js (shared.tsx is prepended to each page).
// Needs @babel/standalone at build time only:  npm i --no-save @babel/standalone
// The browser runs plain JS + vendored React; no CDN, no in-browser Babel.
const fs = require('fs'), path = require('path');
const Babel = require('@babel/standalone');
const src = path.join(__dirname, 'web', 'src'), out = path.join(__dirname, 'web', 'assets');
const shared = fs.readFileSync(path.join(src, 'shared.tsx'), 'utf8');
for (const page of ['flexibility', 'trace', 'discom', 'rural', 'hub']) {
  const code = shared + '\n' + fs.readFileSync(path.join(src, page + '.tsx'), 'utf8');
  const res = Babel.transform(code, {
    filename: page + '.tsx', presets: [['typescript', { isTSX: true, allExtensions: true }], 'react'],
  });
  fs.writeFileSync(path.join(out, page + '.js'), `/* generated from web/src/${page}.tsx by build_tsx.js -- do not edit */\n(function(){\n${res.code}\n})();\n`);
  console.log('built', page + '.js', res.code.length, 'bytes');
}
