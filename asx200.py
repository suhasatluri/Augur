"""ASX 200 ticker validation list. Updated periodically."""

# Top ~200 ASX-listed companies by market cap.
# This is a static validation list — not a live index feed.
ASX200_TICKERS = frozenset({
    # Top 20
    "BHP", "CBA", "CSL", "NAB", "WBC", "ANZ", "WES", "MQG", "FMG", "WDS",
    "TLS", "WOW", "RIO", "ALL", "GMG", "TCL", "COL", "STO", "QBE", "REA",
    # 21-50
    "NCM", "AMC", "SHL", "JHX", "SOL", "ORG", "IAG", "MIN", "S32", "SUN",
    "BXB", "APA", "RMD", "CPU", "TWE", "ORI", "AZJ", "BSL", "SVW", "GPT",
    "NST", "MGR", "DXS", "CHC", "SGP", "VCX", "SCG", "ABP", "CWN", "EVN",
    # 51-100
    "ILU", "WHC", "ALD", "LYC", "PLS", "IGO", "SFR", "29M", "DEG", "GOR",
    "RED", "PRN", "BPT", "AWC", "NHC", "YAL", "CRN", "HVN", "TAH", "SGM",
    "IEL", "ASX", "MPL", "NXT", "ALX", "CEN", "DRR", "CIA", "BOQ", "BEN",
    "HUB", "NWS", "SEK", "CAR", "DHG", "REH", "WTC", "XRO", "TNE", "ALU",
    "PME", "APX", "TYR", "LNK", "PPT", "CGF", "AMP", "IFL", "MFG", "PTM",
    # 101-150
    "PDN", "ERA", "LOT", "BMN", "DYL", "PEN", "AGE", "BOE", "BKY", "VMY",
    "CCX", "OZL", "NIC", "WSA", "PAN", "LTR", "SYR", "TLX", "AGY", "ARU",
    "GQG", "HMC", "JDO", "SDF", "NWL", "QAN", "FLT", "WEB", "CTD", "CVN",
    "KAR", "BRG", "SUL", "JBH", "HVN", "PMV", "MYR", "LOV", "AX1", "SBM",
    "EDV", "RWC", "GWA", "CSR", "ABC", "BLD", "CIM", "DOW", "IPL", "NUF",
    # 151-200
    "ELD", "GNC", "CGC", "AAC", "TGR", "HUO", "SLC", "A2M", "BGA", "ING",
    "RGN", "CLW", "BWP", "CQR", "HDN", "ARF", "HCW", "GDG", "AUB", "SLX",
    "NHF", "HLS", "RHC", "ANN", "COH", "FPH", "EBO", "API", "TER", "IMU",
    "LIC", "WAM", "AFI", "ARG", "MFF", "WLE", "PIC", "ACF", "BKI", "DUI",
    "VEA", "AMP", "ORA", "IPH", "NAN", "PXA", "LRK", "AIS", "SDR", "APM",
})


def is_valid_asx_ticker(ticker: str) -> bool:
    """Check if a ticker is in the ASX 200 list."""
    return ticker.upper() in ASX200_TICKERS
