# Changelog

## [1.3.0](https://github.com/mnfst/manifest-python/compare/v1.2.0...v1.3.0) (2026-09-26)


### Features

* choose which calls reach Manifest with MNFST_ALLOWLIST / MNFST_DENYLIST ([#42](https://github.com/mnfst/manifest-python/issues/42)) ([0ffb856](https://github.com/mnfst/manifest-python/commit/0ffb856bc3634b4ccf37cd47b84ef73de841d621))
* heal and track aiohttp calls ([#43](https://github.com/mnfst/manifest-python/issues/43)) ([d656ff3](https://github.com/mnfst/manifest-python/commit/d656ff35f06e8c68d2fdd259992abc4ba0c69b10))
* heal and track urllib.request calls ([#45](https://github.com/mnfst/manifest-python/issues/45)) ([cba288b](https://github.com/mnfst/manifest-python/commit/cba288bdd621eeb946642cd3a0c8088643cd8c3b))
* heal and track urllib.request calls ([#46](https://github.com/mnfst/manifest-python/issues/46)) ([bebfe4f](https://github.com/mnfst/manifest-python/commit/bebfe4fd77386a2844dda01195f24782a5c0b07f))


### Bug Fixes

* doctor reads the key from the project's .env files ([#41](https://github.com/mnfst/manifest-python/issues/41)) ([06b446b](https://github.com/mnfst/manifest-python/commit/06b446b6d0c747a7eb34ff8efce6d3e55c13d7bf))


### Documentation

* a warmer pitch at the top of the README ([f04f089](https://github.com/mnfst/manifest-python/commit/f04f0899547b7e42938c5aa9991ffabc95711b53))
* lead the README with what Manifest does today ([b9d4293](https://github.com/mnfst/manifest-python/commit/b9d4293dd821f2da51280095c989200ef26b473b))
* lead the README with what Manifest does today ([5b77dd5](https://github.com/mnfst/manifest-python/commit/5b77dd5437c4f746dcf419be429dc694e7b70e0d))
* **readme:** present Manifest as the API resilience layer ([a66e467](https://github.com/mnfst/manifest-python/commit/a66e4677b9e38f5ce964e2825c47529b7217b3c1))
* remove references to the private app repository ([#6](https://github.com/mnfst/manifest-python/issues/6)) ([e16c035](https://github.com/mnfst/manifest-python/commit/e16c035116a636d65e8c19f23d0980656d6db2bd))

## [1.2.0](https://github.com/mnfst/manifest-python/compare/v1.1.0...v1.2.0) (2026-09-23)


### Features

* track every call as metadata ([aa77c6e](https://github.com/mnfst/manifest-python/commit/aa77c6eaa5a5bf36a0c4152084d473953d5decae))
* track every call as metadata ([56b4e62](https://github.com/mnfst/manifest-python/commit/56b4e62433a6a91115e0984959d350fbb5f929c2))


### Bug Fixes

* keep the SDK diagram out of the source distribution ([e37091f](https://github.com/mnfst/manifest-python/commit/e37091f43ea0069339de3a55b36262661f37b771))
* keep the SDK diagram out of the source distribution ([b3476b9](https://github.com/mnfst/manifest-python/commit/b3476b961750bce31c8d8dc95249f865d1599a91))
* make tracking fork-safe and never lose a healable failure ([2255767](https://github.com/mnfst/manifest-python/commit/22557670ebae37187b65c825735962f84532aa65))


### Documentation

* show call tracking in the SDK diagram ([37850cc](https://github.com/mnfst/manifest-python/commit/37850cc46c2d5fcd257a202bfbb2dd8fe859dc8e))

## [1.1.0](https://github.com/mnfst/manifest-python/compare/v1.0.0...v1.1.0) (2026-09-22)


### Features

* add mnfst doctor command ([fb7ecb2](https://github.com/mnfst/manifest-python/commit/fb7ecb201463d6e554b98af6caed1202850e5649))
* add mnfst doctor command ([feeb7d5](https://github.com/mnfst/manifest-python/commit/feeb7d55cff1f3b664c4572ed879b9f9088b7cf9))
* add mnfst run console script ([45ca90c](https://github.com/mnfst/manifest-python/commit/45ca90cad0f330d4216ad69a749c33cffb54bf6f))
* add mnfst run console script ([6a1cfa1](https://github.com/mnfst/manifest-python/commit/6a1cfa1315e3b6aef16ba97ca9ceca36a5f74e69))
* announce the install with a boot handshake ([ccf9b48](https://github.com/mnfst/manifest-python/commit/ccf9b4818d3488ab73dc15c2f369f3c9d5eba580))
* announce the install with a boot handshake ([f866bf3](https://github.com/mnfst/manifest-python/commit/f866bf374123855ff8f85d06cb14870be7e78a0b))


### Bug Fixes

* add rounded corners to logo image (16px border-radius) ([3bfead0](https://github.com/mnfst/manifest-python/commit/3bfead01501e17b7eed2f88bde7ee21d4c9f9ae8))
* apply border-radius with wrapper div for GitHub compatibility ([544b673](https://github.com/mnfst/manifest-python/commit/544b673eaf3b978ab408eb5996bbee479fc3a4a1))
* **doctor:** make the key check a probe, not an install ([5962cdb](https://github.com/mnfst/manifest-python/commit/5962cdb9a6dd08e707e5a6cb3f6d47c1cab80172))
* **doctor:** make the key check a probe, not an install ([7eccff0](https://github.com/mnfst/manifest-python/commit/7eccff0d5ad08e3192e17935402d4ab441339fcc))
* increase border-radius to 56px ([a51bff6](https://github.com/mnfst/manifest-python/commit/a51bff6af42948d1a28f983fe044890f4bd0eb18))
* remove invalid Markdown syntax from H1 ([2e7a261](https://github.com/mnfst/manifest-python/commit/2e7a2615dd34513cae7e1a30c67b34a6febfb0b5))
* send GET retries without a body when the patch is query-only ([77e4601](https://github.com/mnfst/manifest-python/commit/77e4601882b306f3e08bf15ac63426dd4da71380))
* send GET retries without a body when the patch is query-only ([200156b](https://github.com/mnfst/manifest-python/commit/200156b91de4a25d49dadcd0651297b01ebe430c))


### Documentation

* add 'Get started' section for installation code ([ee3d166](https://github.com/mnfst/manifest-python/commit/ee3d1666591453e99e69b917daa8436d9d82540a))
* add 'How it works' section for diagram ([64101d1](https://github.com/mnfst/manifest-python/commit/64101d18eaf806800b37ddbff74c24a00a061ff8))
* add border-radius to header image ([84818d1](https://github.com/mnfst/manifest-python/commit/84818d11196775a31f69d1fd4d59d1b119c9bcb8))
* add community files (CODE_OF_CONDUCT, CONTRIBUTING, SECURITY, LICENSE) ([79148ad](https://github.com/mnfst/manifest-python/commit/79148ad4385b6cd26e14b47c9f37df28a3c19843))
* add community files (CODE_OF_CONDUCT, CONTRIBUTING, SECURITY, LICENSE) ([6203da9](https://github.com/mnfst/manifest-python/commit/6203da9e0ed78033832189a91064de080d65bd5e))
* add Prerequisites section and remove redundant text ([cb1472b](https://github.com/mnfst/manifest-python/commit/cb1472b76c1c4b1663f5a2acf7f652364488b5d6))
* add Python 3.10 download link (opens in new tab) ([36edd72](https://github.com/mnfst/manifest-python/commit/36edd722fbe5d0f443f79311319c1e8e2f5a24d5))
* add section titles for better structure ([cf99641](https://github.com/mnfst/manifest-python/commit/cf9964133c1a64986b95092ca61e60fada38c2dc))
* center header section with separator ([0de4d0d](https://github.com/mnfst/manifest-python/commit/0de4d0dc21133fcd12c54e4440f7cfb75b8d468b))
* change 'in real time' to 'on the fly' ([78fdd92](https://github.com/mnfst/manifest-python/commit/78fdd92b2abf4ff46bdd138c8dce19eb788cce22))
* change 'successful requests' to '2xx' in title ([87ec4a3](https://github.com/mnfst/manifest-python/commit/87ec4a36cb5f3f3de499972fb16d5373aec3e054))
* clarify 'Set the key' refers to environment variable ([60697f8](https://github.com/mnfst/manifest-python/commit/60697f8f7bc67858d807f7d234fbdeaadefa26f0))
* fix comment capitalization and punctuation ([e3587f4](https://github.com/mnfst/manifest-python/commit/e3587f46ac5fcd72ab112c95c204b974633f00eb))
* fix comment formatting in Try it section ([04a1a07](https://github.com/mnfst/manifest-python/commit/04a1a07b92900d3f5522119c65004c5286a5affc))
* offer the agent install path first ([8560e51](https://github.com/mnfst/manifest-python/commit/8560e510aa3ab0002a5e8fdbbcd34a2faf4808e1))
* remove H1 border and separator under badges ([ab4fff7](https://github.com/mnfst/manifest-python/commit/ab4fff74c5d7f0fd8471a9387d26166d6c863386))
* rename section to 'Try it' for better engagement ([20f2116](https://github.com/mnfst/manifest-python/commit/20f2116f336c5fcb2c693435f6d1c9b4bae5458c))
* restructure SDK README with clearer value proposition ([49e264b](https://github.com/mnfst/manifest-python/commit/49e264b8716ba0b7bda78e4a0a4dafb7db36e5a7))
* restructure SDK README with clearer value proposition ([cdb2458](https://github.com/mnfst/manifest-python/commit/cdb245827bd55fa87d25adbc4c8a865bb2eec298))
* simplify Prerequisites to just Python version ([a46eb9c](https://github.com/mnfst/manifest-python/commit/a46eb9c31c703d6dc31118158ab5b2bde851be4e))
* simplify README - rename section and remove Good to know ([bb75075](https://github.com/mnfst/manifest-python/commit/bb7507587aa55c9809f73edef5fe6853374822bb))
* simplify Setup section wording ([0ad3a5a](https://github.com/mnfst/manifest-python/commit/0ad3a5a57645eea4c1d8f0dfc6b75cdbd34e252e))

## [1.0.0](https://github.com/mnfst/manifest-python/compare/v0.2.0...v1.0.0) (2026-09-14)


### ⚠ BREAKING CHANGES

* mnfst.flush is no longer exported.

### Features

* instrument httpx2 transports ([103e640](https://github.com/mnfst/manifest-python/commit/103e6404ce713923f343e2632bc491fe9e408f0d))
* instrument httpx2 transports ([230fa9f](https://github.com/mnfst/manifest-python/commit/230fa9f5ab693ed2892bc2967bed9794779a643e))


### Code Refactoring

* remove flush from the SDK ([88874af](https://github.com/mnfst/manifest-python/commit/88874afeffddb1397a5eff348398987fa89c8c44))

## [0.2.0](https://github.com/mnfst/manifest-python/compare/v0.1.0...v0.2.0) (2026-09-10)


### Features

* capture any request-side 4xx instead of an allowlist ([f60dc8d](https://github.com/mnfst/manifest-python/commit/f60dc8dc097dfc3d93c5e8c10d73d08b5ee0adaf))
* capture any request-side 4xx instead of an allowlist ([2e010af](https://github.com/mnfst/manifest-python/commit/2e010af34e4712cab85e678357e5de0781989f77))
* heal form-urlencoded request bodies ([f0d95dd](https://github.com/mnfst/manifest-python/commit/f0d95dd34d961995a26abad75f020a0e29a9c667))
* heal form-urlencoded request bodies ([4819ac7](https://github.com/mnfst/manifest-python/commit/4819ac7584e2de9c413cbc616a61daa2fe24359b))


### Bug Fixes

* match HTTPAdapter.send signature in requests patch ([c4e4050](https://github.com/mnfst/manifest-python/commit/c4e40508e0d797f9e5f155a17c5be81ff3f2b1a5))
* match HTTPAdapter.send signature in requests patch ([51802c1](https://github.com/mnfst/manifest-python/commit/51802c13be80b009932a8d67d12baa1577957a09))


### Documentation

* add PyPI package badges ([ff9ab0e](https://github.com/mnfst/manifest-python/commit/ff9ab0eff6d8fc2e40342950053b789d4c352423))
* add PyPI package badges ([646c8b6](https://github.com/mnfst/manifest-python/commit/646c8b6bd4d8333dd9093cbf9fed7ef88d5caae1))
* simplify README and add healing diagram ([19ee7ed](https://github.com/mnfst/manifest-python/commit/19ee7ed5dfd2ee0a428fcca2b56c3366c571f7e9))
* simplify README and add healing diagram ([aa6838d](https://github.com/mnfst/manifest-python/commit/aa6838d8da3cc700d12a9c56281697ebb6638dfc))
