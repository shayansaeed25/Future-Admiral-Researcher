import numpy as np, time
def synth(n=4000, seed=1, start_price=100.0, start_ts=1_700_000_000_000 - (1_700_000_000_000 % 86_400_000)):
    rng = np.random.default_rng(seed)
    # regime-switching drift: trend up / trend down / chop
    drift = np.zeros(n); i=0
    while i<n:
        L = rng.integers(150, 600); mode = rng.choice([-1,0,1], p=[.3,.3,.4])
        drift[i:i+L] = mode*0.0004; i+=L
    ret = drift + rng.normal(0, 0.006, n)
    close = start_price*np.exp(np.cumsum(ret))
    open_ = np.concatenate(([start_price], close[:-1]))
    hi = np.maximum(open_, close)*(1+np.abs(rng.normal(0,0.003,n)))
    lo = np.minimum(open_, close)*(1-np.abs(rng.normal(0,0.003,n)))
    vol = rng.lognormal(5, 0.4, n)*(1+3*(np.abs(ret)>0.012))
    ts = start_ts + np.arange(n)*3_600_000
    return {"ts":ts.astype(float),"o":open_,"h":hi,"l":lo,"c":close,"v":vol}
