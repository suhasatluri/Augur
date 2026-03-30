export const ASX200_TICKERS = [
  "A2M","AAC","ABC","ABP","ACF","AFI","AGE","AGY","AIS","ALD",
  "ALL","ALU","ALX","AMP","AMC","ANN","ANZ","APA","APM","APX",
  "ARF","ARG","ARU","ASX","AUB","AWC","AX1","AZJ","BEN","BGA",
  "BHP","BKI","BKY","BLD","BMN","BOE","BOQ","BPT","BRG","BSL",
  "BXB","BWP","CAR","CBA","CCX","CEN","CGC","CGF","CHC","CIA",
  "CIM","CLW","COH","COL","CPU","CQR","CRN","CSL","CSR","CTD",
  "CVN","CWN","DEG","DFG","DHG","DOW","DRR","DUI","DXS","DYL",
  "EBO","EDV","ELD","ERA","EVN","FLT","FMG","FPH","GDG","GNC",
  "GOR","GPT","GQG","GWA","HCW","HDN","HLS","HMC","HUB","HUO",
  "HVN","IAG","IEL","IFL","IGO","ILU","IMU","ING","IPH","IPL",
  "JBH","JDO","JHX","KAR","LIC","LNK","LOT","LOV","LRK","LTR",
  "LYC","MFF","MFG","MGR","MIN","MPL","MQG","MYR","NAB","NAN",
  "NCM","NHC","NHF","NIC","NST","NUF","NWL","NWS","NXT","ORA",
  "ORG","ORI","OZL","PAN","PDN","PEN","PIC","PLS","PME","PMV",
  "PPT","PRN","PTM","PXA","QAN","QBE","REA","RED","REH","RGN",
  "RHC","RIO","RMD","RWC","S32","SBM","SCG","SDF","SDR","SEK",
  "SFR","SGM","SGP","SHL","SLC","SLX","SOL","STO","SUL","SUN",
  "SVW","SYR","TAH","TER","TGR","TLX","TLS","TNE","TWE","TCL",
  "TYR","VCX","VEA","VMY","WAM","WBC","WDS","WEB","WES","WHC",
  "WLE","WOW","WSA","WTC","XRO","YAL","29M",
] as const;

export type ASXTicker = (typeof ASX200_TICKERS)[number];
