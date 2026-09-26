# Third-party notices

The recorder bundle, `src/oss_clarity/static/oss_clarity/recorder.js`, is built
from `js/recorder/` and includes code from the packages below. Each is
distributed under the MIT licence, reproduced once at the end.

| Package | Version | Copyright |
|---|---|---|
| `@rrweb/record` | 2.1.6 | Copyright (c) 2018 Contributors (https://github.com/rrweb-io/rrweb/graphs/contributors) and SmartX Inc. |
| `@rrweb/types` | 2.1.6 | Copyright (c) 2018 Contributors (https://github.com/rrweb-io/rrweb/graphs/contributors) and SmartX Inc. |

`@rrweb/record` is itself a bundle. It contains code from `rrweb`,
`rrweb-snapshot` and `@rrweb/utils` (same copyright as above) and from
`base64-arraybuffer` (Copyright (c) 2012 Niklas von Hertzen), all MIT.

The build (`js/recorder/build.mjs`) fails if it bundles a package that is not
named in this file.

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
