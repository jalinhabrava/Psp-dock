# Android TV binary distribution decision

The Android TV integration now follows Windows `main` at
`ff3033351bd9901041eb6b42a639d1810e85bb81`, which includes the Windows v0.1.3
release line and the native PSP 0308 contract. That baseline introduced a
licensing condition that was not present when the first Android prototype was
built.

## Existing terms

The repository's [LICENSE.md](../LICENSE.md) applies PolyForm Noncommercial
1.0.0 to original project code unless a specific exception applies.
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) preserves third-party
licenses and says that a combined executable must satisfy all applicable
licenses. It does not currently grant a GPL-compatible license to the original
Android application or its shared session module.

PPSSPP is [GPL version 2 or later](https://www.ppsspp.org/docs/development/getting-started/).
This build incorporates its Java classes and native library into the same APK,
with direct JNI calls and a shared application/session integration. The
[GNU licensing FAQ](https://www.gnu.org/licenses/gpl-faq.html#NoMilitary)
explains that a noncommercial restriction cannot be added to a GPL-covered
combined program. Including the upstream license file alone does not resolve
the original Android code's different terms.

## Concrete proposal for the project owner

Approve a GPL-3.0-or-later exception **only for original code under
`android-tv/`**, including the Dock application, session contract, integration
layer and build scripts. Keep the existing PPSSPP and dependency copyright and
license notices. PPSSPP's GPL-2.0-or-later terms permit choosing a later GPL
version for the combination.

This would leave the separate Windows/Linux host, project branding and other
materials outside that directory under their current terms. Third-party
materials already retain their own licenses. The scope of any artwork included
in a released APK must also be checked when preparing that release.

**This document is a proposal, not a license grant.** No repository license has
been replaced and no Android-specific exception has been applied. The project
owner must approve the scope before the original Android source is offered
under the proposed terms.

## Build and release boundary

Development and private evaluation can continue. CI compiles both ARM ABIs,
runs the tests and lint, and verifies the resulting APK. Its validation report
contains the APK hash and native-library information. Public APK upload is
disabled unless the repository variable
`ANDROID_TV_DISTRIBUTION_READY` is explicitly set to `true` after the licensing
decision has been applied and the source distribution has been prepared.

Before distributing a combined APK:

1. Record the owner's decision and apply the agreed Android-specific license
   notices and full license text.
2. Provide the complete corresponding source for the exact APK, including the
   pinned PPSSPP revision, recursive dependencies, integration patches and
   reproducible build instructions. A link to an unrelated upstream release
   does not describe this modified application.
3. Preserve all third-party notices and publish the APK and source checksums
   together.
4. Enable binary distribution only after those concrete items are reviewed.

This decision is independent of physical Android TV/PSP acceptance. A passing
software build does not establish USB, display, controller or save behavior on
a particular television.
