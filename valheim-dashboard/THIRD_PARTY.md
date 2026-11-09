# Third-party notices and references

- **Valheim / Iron Gate:** server binaries and managed assemblies must be downloaded
  separately using SteamCMD. They are runtime/build references, not redistributed here.
- **BepInExPack Valheim:** https://thunderstore.io/c/valheim/p/denikson/BepInExPack_Valheim/
  is downloaded separately. BepInEx and its components retain their respective licenses;
  see the downloaded package and https://github.com/BepInEx/BepInEx.
- **ValheimBakaLoader format documentation:**
  https://github.com/RyanDMcAfee/ValheimBakaLoader/blob/main/ValheimBakaLoader/Tools/Atlas/ChunkedWorldReader.cs
  was consulted for the chunked file layout. The Python parser is a separate
  interoperability implementation. No BakaLoader executable or C# implementation is
  bundled. Its source-available license is retained in
  [docs/licenses](../docs/licenses/ValheimBakaLoader-LICENSE).
- **AFKManager architecture:** https://github.com/Torokal/AFKManager/blob/main/docs/ARCHITECTURE.md
  informed the server-only ready-peer/ZDO observation approach; its components are not bundled.
- **Progression reference:** https://corpus.gg/games/valheim/tools/progression
  documents the final-stage Deep North progression flag.
- **Cinzel and Manrope:** self-hosted font files in `static/fonts/` include their
  accompanying SIL Open Font License texts. Public upstream author/copyright
  notices are preserved.
- **.NET SDK:** a separate build dependency, not redistributed in this repository.
