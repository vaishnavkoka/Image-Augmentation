# assets

Files the tool ships rather than discovers on the host.

## icc/ — 49 ICC profiles

The `profile` filter used to offer a different number of options depending on what the
host had installed: 51 here with `colord-data` and `libgs9-common` present, 14 on a
machine without them. Every profile the tool offers is now bundled, so the catalogue
is identical everywhere and a generated corpus is reproducible across machines.

They come from three places:

| Source | Count | Licence |
|---|---|---|
| `colord-data`, Debian | 23 | **CC0** — public domain dedication (`data/profiles/*` in its copyright file) |
| `libgs9-common`, Ghostscript | 13 | **Expat/MIT** with the SunSoft exception |
| `RE4BDD/DALL-E` survey | 13 | broadcast and press profiles, see below |

Both system licences permit redistribution. `Crayons.icc` and `x11-colors.icc` were
excluded: they are named-colour tables (`nmcl`), which ImageMagick refuses as a
conversion target.

### The 13 from the DALL-E survey

`RE4BDD/DALL-E/Discrete_filters/ICC profiles` held 128 files. Only 13 were taken, and
the reasons matter:

| | Count | |
|---|---|---|
| Copied | **13** | valid ICC, usable as a conversion target, not already present on this host |
| Rejected — zero-filled | **105** | carry no ICC signature at all. `sRGB Color Space Profile.icm` was 720 KB of nothing; the 128 files totalled 549 MB, almost entirely empty bytes |
| Rejected — already present | 3 | AdobeRGB1998, AppleRGB, ColorMatchRGB come from `colord-data` |
| Rejected — duplicates | 7 | the same profile filed under several folders |

Every copied file was checked for the `acsp` signature at byte offset 36 and for a
device class in `{scnr, mntr, prtr, link, spac, abst}` — ImageMagick refuses anything
else as a conversion target. `_discover_profiles()` re-applies both checks at startup,
so a bad file added here later is skipped rather than offered.

Total 11 MB across all 49, dominated by three large CMYK press profiles.

| Profile | Class | Space | Notes |
|---|---|---|---|
| APTEC_PC10_CardBoard_2023_v1 | prtr | CMYK | packaging board press |
| CNZ006, CNZ007 | mntr | RGB | monitor profiles |
| CoatedFOGRA27, WebCoatedFOGRA28 | prtr | CMYK | European offset standards |
| GRACoL2006_Coated1v2, SNAP2007, RSWOP | prtr | CMYK | US press standards |
| PAL_SECAM, SMPTE-C | mntr | RGB | broadcast primaries |
| VideoHD, VideoNTSC, VideoPAL | scnr | RGB | video capture |

Broadcast and press profiles are the interesting ones for origin classification:
they impose gamut and transfer characteristics a camera never would.
