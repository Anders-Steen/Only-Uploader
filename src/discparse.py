import os
import shutil
import traceback
import asyncio
from glob import glob
from pymediainfo import MediaInfo
from collections import OrderedDict
import json

from src.console import console


class DiscParse():
    def __init__(self):
        pass

    """
    Locate the bdinfo-rs binary: PATH first, then a copy in bin/
    """
    def get_bdinfo_bin(self, base_dir):
        found = shutil.which("bdinfo-rs")
        if found:
            return found
        for name in ("bdinfo-rs", "bdinfo-rs.exe"):
            local = os.path.join(base_dir, "bin", name)
            if os.path.exists(local):
                return local
        return None

    """
    Ask bdinfo-rs for the playlist table and pick the longest playlist,
    which is the disc's main feature (what BDInfo scanned by default)
    """
    async def get_main_playlist(self, binary, path):
        proc = await asyncio.create_subprocess_exec(
            binary, path, "--list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        output, _ = await proc.communicate()
        main_playlist = None
        longest = -1
        for line in output.decode("utf-8", "replace").splitlines():
            # "  #  Group  Playlist File  Length  Estimated Bytes  Measured Bytes"
            # Match the .mpls name and the first H:MM:SS after it, so the
            # column position of the length doesn't matter.
            fields = line.split()
            playlist = next((f for f in fields if f.lower().endswith(".mpls")), None)
            if playlist is None:
                continue
            length = None
            for candidate in fields[fields.index(playlist) + 1:]:
                try:
                    hours, minutes, seconds = (int(part) for part in candidate.split(":"))
                except ValueError:
                    continue
                length = hours * 3600 + minutes * 60 + seconds
                break
            if length is None or length <= longest:
                continue
            longest = length
            main_playlist = playlist
        return main_playlist

    """
    Run bdinfo-rs on a disc and write its report into save_dir
    """
    async def scan_disc(self, binary, path, save_dir):
        main_playlist = await self.get_main_playlist(binary, path)
        if main_playlist is None:
            console.print(f"[bold yellow]No playlist listed for {path}, scanning the whole disc")
            args = [binary, path, save_dir, "--no-banner", "--whole"]
        else:
            args = [binary, path, save_dir, "--no-banner", "--mpls", main_playlist]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        await proc.communicate()
        return proc.returncode

    """
    Get and parse bdinfo
    """
    async def get_bdinfo(self, discs, folder_id, base_dir, meta_discs):
        save_dir = f"{base_dir}/tmp/{folder_id}"
        if not os.path.exists(save_dir):
            os.mkdir(save_dir)
        for i in range(len(discs)):
            bdinfo_text = None
            path = os.path.abspath(discs[i]['path'])
            for file in os.listdir(save_dir):
                if file == f"BD_SUMMARY_{str(i).zfill(2)}.txt":
                    bdinfo_text = save_dir + "/" + file
            if bdinfo_text is None or meta_discs == []:
                if os.path.exists(f"{save_dir}/BD_FULL_{str(i).zfill(2)}.txt"):
                    bdinfo_text = os.path.abspath(f"{save_dir}/BD_FULL_{str(i).zfill(2)}.txt")
                else:
                    bdinfo_text = ""
                    bdinfo_bin = self.get_bdinfo_bin(base_dir)
                    if bdinfo_bin is None:
                        raise RuntimeError("bdinfo-rs not found, install it or drop it in bin/")
                    console.print(f"[bold green]Scanning {path}")
                    returncode = await self.scan_disc(bdinfo_bin, path, save_dir)
                    if returncode == 4:
                        raise RuntimeError(f"AACS-encrypted disc, bdinfo-rs will not scan {path}")
                    if returncode not in (0, 3):
                        raise RuntimeError(f"bdinfo-rs failed scanning {path} (exit code {returncode})")
                attempts = 0
                while True:
                    attempts += 1
                    try:
                        if bdinfo_text == "":
                            reports = glob(f"{save_dir}/BDINFO*.txt")
                            if reports:
                                bdinfo_text = max(reports, key=os.path.getmtime)
                        with open(bdinfo_text, 'r') as f:
                            text = f.read()
                            result = text.split("QUICK SUMMARY:", 2)
                            files = result[0].split("FILES:", 2)[1].split("CHAPTERS:", 2)[0].split("-------------")
                            result2 = result[1].rstrip(" \n")
                            result = result2.split("********************", 1)
                            bd_summary = result[0].rstrip(" \n")
                            f.close()
                        with open(bdinfo_text, 'r') as f:  # parse extended BDInfo
                            text = f.read()
                            result = text.split("[code]", 3)
                            result2 = result[2].rstrip(" \n")
                            result = result2.split("FILES:", 1)
                            ext_bd_summary = result[0].rstrip(" \n")
                            f.close()
                        try:
                            shutil.copyfile(bdinfo_text, f"{save_dir}/BD_FULL_{str(i).zfill(2)}.txt")
                            os.remove(bdinfo_text)
                        except shutil.SameFileError:
                            pass
                    except Exception:
                        if attempts >= 3:
                            raise
                        console.print(traceback.format_exc())
                        await asyncio.sleep(5)
                        continue
                    break
                with open(f"{save_dir}/BD_SUMMARY_{str(i).zfill(2)}.txt", 'w') as f:
                    f.write(bd_summary.strip())
                    f.close()
                with open(f"{save_dir}/BD_SUMMARY_EXT.txt", 'w') as f:  # write extended BDInfo file
                    f.write(ext_bd_summary.strip())
                    f.close()

                bdinfo = self.parse_bdinfo(bd_summary, files[1], path)

                discs[i]['summary'] = bd_summary.strip()
                discs[i]['bdinfo'] = bdinfo
                # shutil.rmtree(f"{base_dir}/tmp")
            else:
                discs = meta_discs

        return discs, discs[0]['bdinfo']

    def parse_bdinfo(self, bdinfo_input, files, path):
        bdinfo = dict()
        bdinfo['video'] = list()
        bdinfo['audio'] = list()
        bdinfo['subtitles'] = list()
        bdinfo['path'] = path
        lines = bdinfo_input.splitlines()
        for l in lines:  # noqa E741
            line = l.strip().lower()
            if line.startswith("*"):
                line = l.replace("*", "").strip().lower()
            if line.startswith("playlist:"):
                playlist = l.split(':', 1)[1]
                bdinfo['playlist'] = playlist.split('.', 1)[0].strip()
            if line.startswith("disc size:"):
                size = l.split(':', 1)[1]
                size = size.split('bytes', 1)[0].replace(',', '')
                size = float(size) / float(1 << 30)
                bdinfo['size'] = size
            if line.startswith("length:"):
                length = l.split(':', 1)[1]
                bdinfo['length'] = length.split('.', 1)[0].strip()
            if line.startswith("video:"):
                split1 = l.split(':', 1)[1]
                split2 = split1.split('/', 12)
                while len(split2) != 9:
                    split2.append("")
                n = 0
                if "Eye" in split2[2].strip():
                    n = 1
                    three_dim = split2[2].strip()
                else:
                    three_dim = ""
                try:
                    bit_depth = split2[n + 6].strip()
                    hdr_dv = split2[n + 7].strip()
                    color = split2[n + 8].strip()
                except Exception:
                    bit_depth = ""
                    hdr_dv = ""
                    color = ""
                bdinfo['video'].append({
                    'codec': split2[0].strip(),
                    'bitrate': split2[1].strip(),
                    'res': split2[n + 2].strip(),
                    'fps': split2[n + 3].strip(),
                    'aspect_ratio': split2[n + 4].strip(),
                    'profile': split2[n + 5].strip(),
                    'bit_depth': bit_depth,
                    'hdr_dv': hdr_dv,
                    'color': color,
                    '3d': three_dim,
                })
            elif line.startswith("audio:"):
                if "(" in l:
                    l = l.split("(")[0]  # noqa E741
                l = l.strip()  # noqa E741
                split1 = l.split(':', 1)[1]
                split2 = split1.split('/')
                n = 0
                if "Atmos" in split2[2].strip():
                    n = 1
                    fuckatmos = split2[2].strip()
                else:
                    fuckatmos = ""
                try:
                    bit_depth = split2[n + 5].strip()
                except Exception:
                    bit_depth = ""
                bdinfo['audio'].append({
                    'language': split2[0].strip(),
                    'codec': split2[1].strip(),
                    'channels': split2[n + 2].strip(),
                    'sample_rate': split2[n + 3].strip(),
                    'bitrate': split2[n + 4].strip(),
                    'bit_depth': bit_depth,  # Also DialNorm, but is not in use anywhere yet
                    'atmos_why_you_be_like_this': fuckatmos,
                })
            elif line.startswith("disc title:"):
                title = l.split(':', 1)[1]
                bdinfo['title'] = title
            elif line.startswith("disc label:"):
                label = l.split(':', 1)[1]
                bdinfo['label'] = label
            elif line.startswith('subtitle:'):
                split1 = l.split(':', 1)[1]
                split2 = split1.split('/')
                bdinfo['subtitles'].append(split2[0].strip())
        files = files.splitlines()
        bdinfo['files'] = []
        for line in files:
            try:
                stripped = line.split()
                m2ts = {}
                bd_file = stripped[0]
                time_in = stripped[1]  # noqa F841
                bd_length = stripped[2]
                bd_size = stripped[3]  # noqa F841
                bd_bitrate = stripped[4]  # noqa F841
                m2ts['file'] = bd_file
                m2ts['length'] = bd_length
                bdinfo['files'].append(m2ts)
            except Exception:
                pass
        return bdinfo

    """
    Parse VIDEO_TS and get mediainfos
    """
    async def get_dvdinfo(self, discs):
        for each in discs:
            path = each.get('path')
            os.chdir(path)
            files = glob("VTS_*.VOB")
            files.sort()
            filesdict = OrderedDict()
            main_set = []
            for file in files:
                trimmed = file[4:]
                if trimmed[:2] not in filesdict:
                    filesdict[trimmed[:2]] = []
                filesdict[trimmed[:2]].append(trimmed)
            main_set_duration = 0
            for vob_set in filesdict.values():
                try:
                    vob_set_mi = MediaInfo.parse(f"VTS_{vob_set[0][:2]}_0.IFO", output='JSON')
                    vob_set_mi = json.loads(vob_set_mi)
                    tracks = vob_set_mi.get('media', {}).get('track', [])
                    if len(tracks) > 1:
                        vob_set_duration = tracks[1].get('Duration', "Unknown")
                    else:
                        console.print("Warning: Expected track[1] is missing.")
                        vob_set_duration = "Unknown"

                except Exception as e:
                    console.print(f"Error processing VOB set: {e}")
                    vob_set_duration = "Unknown"

                if vob_set_duration == "Unknown" or not vob_set_duration.replace('.', '', 1).isdigit():
                    console.print(f"Skipping VOB set due to invalid duration: {vob_set_duration}")
                    continue

                vob_set_duration_float = float(vob_set_duration)

                # If the duration of the new vob set > main set by more than 10%, it's the new main set
                # This should make it so TV shows pick the first episode
                if (vob_set_duration_float * 1.00) > (float(main_set_duration) * 1.10) or len(main_set) < 1:
                    main_set = vob_set
                    main_set_duration = vob_set_duration_float

            each['main_set'] = main_set
            set = main_set[0][:2]
            each['vob'] = vob = f"{path}/VTS_{set}_1.VOB"
            each['ifo'] = ifo = f"{path}/VTS_{set}_0.IFO"
            each['vob_mi'] = MediaInfo.parse(os.path.basename(vob), output='STRING', full=False, mediainfo_options={'inform_version': '1'}).replace('\r\n', '\n')
            each['ifo_mi'] = MediaInfo.parse(os.path.basename(ifo), output='STRING', full=False, mediainfo_options={'inform_version': '1'}).replace('\r\n', '\n')
            each['vob_mi_full'] = MediaInfo.parse(vob, output='STRING', full=False, mediainfo_options={'inform_version': '1'}).replace('\r\n', '\n')
            each['ifo_mi_full'] = MediaInfo.parse(ifo, output='STRING', full=False, mediainfo_options={'inform_version': '1'}).replace('\r\n', '\n')

            size = sum(os.path.getsize(f) for f in os.listdir('.') if os.path.isfile(f)) / float(1 << 30)
            if size <= 7.95:
                dvd_size = "DVD9"
                if size <= 4.37:
                    dvd_size = "DVD5"
            each['size'] = dvd_size
        return discs

    async def get_hddvd_info(self, discs):
        for each in discs:
            path = each.get('path')
            os.chdir(path)
            files = glob("*.EVO")
            size = 0
            largest = files[0]
            # get largest file from files
            for file in files:
                file_size = os.path.getsize(file)
                if file_size > size:
                    largest = file
                    size = file_size
            each['evo_mi'] = MediaInfo.parse(os.path.basename(largest), output='STRING', full=False, mediainfo_options={'inform_version': '1'})
            each['largest_evo'] = os.path.abspath(f"{path}/{largest}")
        return discs
