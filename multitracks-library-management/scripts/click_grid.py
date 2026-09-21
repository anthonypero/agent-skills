import sys, numpy as np
from scipy.io import wavfile
sr, d = wavfile.read(sys.argv[1])
x = d.astype(np.float32)
if x.ndim > 1: x = x.mean(axis=1)
x = np.abs(x) / (np.abs(x).max() or 1)
# envelope: 1 ms max-pool
w = int(sr*0.001)
env = x[:len(x)//w*w].reshape(-1, w).max(axis=1)  # 1 kHz envelope
thr = 0.25
above = env > thr
rise = np.where(above[1:] & ~above[:-1])[0] + 1
# min gap 120 ms
on = []
for r in rise:
    if not on or r - on[-1] > 120: on.append(r)
on = np.array(on) / 1000.0  # seconds
print("clicks:", len(on), " first %.3f  last %.3f" % (on[0], on[-1]))
iv = np.diff(on)
print("interval median %.4f s  -> %.3f BPM" % (np.median(iv), 60/np.median(iv)))
print("interval min %.4f max %.4f std %.4f" % (iv.min(), iv.max(), iv.std()))
# histogram of intervals
vals, cnt = np.unique(np.round(iv, 2), return_counts=True)
print("interval histogram (s: count):", {float(v): int(c) for v, c in zip(vals, cnt) if c > 2})
# fit a constant grid on the dominant interval and measure drift
med = np.median(iv)
k = np.round((on - on[0]) / med)
A = np.vstack([k, np.ones_like(k)]).T
slope, inter = np.linalg.lstsq(A, on, rcond=None)[0]
res = on - (slope*k + inter)
print("best-fit constant tempo %.4f BPM, max |residual| %.1f ms, rms %.1f ms" % (60/slope, np.abs(res).max()*1000, res.std()*1000))
# show local tempo per 8-click window
print("local BPM every 16 clicks:")
for i in range(0, len(on)-16, 16):
    seg = on[i:i+17]
    print("  %6.2f s  %.2f BPM" % (seg[0], 60*16/(seg[-1]-seg[0])))
# largest residual locations
idx = np.argsort(-np.abs(res))[:8]
print("largest residuals:", [(round(float(on[i]),3), round(float(res[i]*1000),1)) for i in sorted(idx)])
