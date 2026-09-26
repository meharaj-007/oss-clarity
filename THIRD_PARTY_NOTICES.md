# Third-party notices

Two committed bundles include third-party code:

- `src/oss_clarity/static/oss_clarity/recorder.js`, built from `js/recorder/`
  (runs on tracked sites);
- `src/oss_clarity/static/oss_clarity/player.js` and `player.css`, built from
  `js/player/` (runs in the Django admin).

The build (`js/recorder/build.mjs`) fails if a bundle includes a package that is
not named in this file.

## Packages bundled directly

| Package | Version | In | Licence |
|---|---|---|---|
| `@rrweb/record` | 2.1.6 | recorder | MIT |
| `@rrweb/types` | 2.1.6 | recorder | MIT |
| `@rrweb/replay` | 2.1.6 | player | MIT |

rrweb copyright: Copyright (c) 2018 Contributors
(https://github.com/rrweb-io/rrweb/graphs/contributors) and SmartX Inc.

## Code inside those packages

`@rrweb/record` and `@rrweb/replay` are themselves pre-built bundles. Between
them they contain code from:

| Package | Licence | Copyright |
|---|---|---|
| `rrweb`, `rrweb-snapshot`, `rrdom`, `@rrweb/utils` | MIT | as rrweb above |
| `postcss` | MIT | Copyright 2013 Andrey Sitnik |
| `nanoid` | MIT | Copyright 2017 Andrey Sitnik |
| `mitt` | MIT | Copyright (c) 2021 Jason Miller |
| `@xstate/fsm` | MIT | Copyright (c) 2015 David Khourshid |
| `base64-arraybuffer` | MIT | Copyright (c) 2012 Niklas von Hertzen |
| `picocolors` | ISC | Copyright (c) 2021-2024 Oleksii Raspopov, Kostiantyn Denysov, Anton Verinov |
| `tslib` helpers | 0BSD | Copyright (c) Microsoft Corporation |

## MIT licence

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## ISC licence

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

## 0BSD licence

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
