#!/usr/bin/env python3
"""Render the library comparison as a figure: outputs on top, difference maps
below, against the ImageMagick command line as ground truth."""
import io, json, os, subprocess, tempfile, sys
import numpy as np
from PIL import Image
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
MAGICK = os.path.join(os.environ.get('MAGICK_HOME', ''), 'bin', 'magick')
SRC = os.path.join(HERE, '..', 'tests', 'oracle_source.png')
d = tempfile.mkdtemp(prefix='imt-fig-')

def cli(args, tag):
    out = os.path.join(d, tag + '.png')
    subprocess.run([MAGICK, SRC] + args + [out], capture_output=True)
    return np.array(Image.open(out).convert('RGB'), np.int16)

def tool(mut, params):
    b = subprocess.run(['curl','-s','-X','POST',BASE+'/api/mutate','-F','image=@'+SRC,
        '-F','mutation='+mut,'-F','parameters='+json.dumps(params)],
        capture_output=True, text=True).stdout
    raw = subprocess.run(['curl','-s',json.loads(b)['result_url']],capture_output=True).stdout
    return np.array(Image.open(io.BytesIO(raw)).convert('RGB'), np.int16)

arr = np.array(Image.open(SRC).convert('RGB'))
import albumentations as A, torch, torchvision.transforms.v2.functional as TF
t = lambda a: torch.from_numpy(a).permute(2,0,1)
back = lambda x: x.permute(1,2,0).numpy().astype(np.int16)

blocks = []
ref = cli(['-blur','2.0x5.0'], 'b')
alb = A.GaussianBlur(blur_limit=(0,0), sigma_limit=(5,5), p=1.0)(image=arr)['image'].astype(np.int16)
tv  = back(TF.gaussian_blur(t(arr), kernel_size=21, sigma=5.0))
blocks.append(('Gaussian blur  σ=5', ref, tool('blur',{'sigma':5}), alb, tv))

ref = cli(['-grayscale','Rec709Luma'], 'g')
a2 = A.ToGray(p=1.0)(image=arr)['image']
if a2.ndim == 2: a2 = np.stack([a2]*3, -1)
tv2 = back(TF.rgb_to_grayscale(t(arr), num_output_channels=3))
blocks.append(('Grayscale  Rec709Luma', ref, tool('grayscale',{'method':'Rec709Luma'}),
               a2.astype(np.int16), tv2))

ref = cli(['-background','white','-rotate','90'], 'r')
a3 = A.Rotate(limit=(90,90), p=1.0)(image=arr)['image'].astype(np.int16)
tv3 = back(TF.rotate(t(arr), 90, expand=True))
blocks.append(('Rotate  90°', ref, tool('rotate',{'degrees':90}), a3, tv3))

names = ['ImageMagick CLI\n(ground truth)', 'This tool', 'albumentations 2.0.8', 'torchvision 0.29']
fig, axes = plt.subplots(6, 4, figsize=(13.0, 17.5))
for bi, (title, ref, ours, alb, tv) in enumerate(blocks):
    imgs = [ref, ours, alb, tv]
    for c in range(4):
        ax = axes[bi*2][c]
        im = imgs[c]
        ax.imshow(np.clip(im,0,255).astype(np.uint8))
        ax.set_xticks([]); ax.set_yticks([])
        if bi == 0: ax.set_title(names[c], fontsize=11, pad=9)
        if c == 0:
            ax.set_ylabel(title, fontsize=12, fontweight='bold', labelpad=12)
        for s in ax.spines.values(): s.set_linewidth(0.8); s.set_color('#94a3b8')

    axes[bi*2+1][0].axis('off')
    axes[bi*2+1][0].text(0.5, 0.55, 'difference from\nground truth  →',
                         ha='center', va='center', fontsize=11, color='#475569',
                         transform=axes[bi*2+1][0].transAxes)
    for c, im in enumerate(imgs[1:], start=1):
        ax = axes[bi*2+1][c]
        if im.shape != ref.shape:
            ax.text(0.5,0.5,'different shape', ha='center', va='center'); ax.axis('off'); continue
        dif = np.abs(ref-im).max(axis=2)
        mx = int(dif.max())
        ax.imshow(dif, cmap='inferno', vmin=0, vmax=max(mx,1))
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_xlabel(('identical — max diff 0' if mx==0 else f'max diff {mx}'),
                      fontsize=10.5, color=('#15803d' if mx==0 else '#b91c1c'),
                      fontweight='bold', labelpad=6)
        for s in ax.spines.values(): s.set_linewidth(0.8); s.set_color('#94a3b8')

fig.suptitle('Does each implementation apply the operator it names?\n'
             'Black in a difference map means agreement with the ImageMagick command line',
             fontsize=13, y=0.995)
fig.tight_layout(rect=[0,0,1,0.975])
out = os.path.join(HERE, '..', 'reports', 'figures', 'fig8_library_comparison.png')
fig.savefig(out, dpi=130, bbox_inches='tight')
print('  wrote', os.path.relpath(out, os.path.join(HERE,'..')))
