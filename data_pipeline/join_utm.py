import logging
import pandas as pd

logger = logging.getLogger(__name__)


def join_campaign_data(ads_df: pd.DataFrame, deals_df: pd.DataFrame,
                       funnel_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    Join de gastos de Ads (por dia) com negócios do HubSpot por utm_campaign.
    Mantém uma linha por (date, channel, campaign_utm) — sem agregar dias.
    """
    if ads_df.empty:
        logger.warning("ads_df vazio — retornando DataFrame vazio")
        return pd.DataFrame()

    # Garantir que a coluna date existe
    if "date" not in ads_df.columns:
        ads_df = ads_df.copy()
        ads_df["date"] = ""

    # Join com HubSpot a nível de campanha (sem data — receita acumulada por UTM)
    if not deals_df.empty and "utm_campaign_origem" in deals_df.columns:
        deals_agg = (
            deals_df.groupby("utm_campaign_origem", as_index=False)
            .agg(
                revenue=("amount", "sum"),
                deals_count=("id", "count"),
            )
            .rename(columns={"utm_campaign_origem": "campaign_utm"})
        )
        merged = ads_df.merge(deals_agg, on="campaign_utm", how="left")
    else:
        merged = ads_df.copy()
        merged["revenue"] = 0.0
        merged["deals_count"] = 0

    merged["revenue"]     = merged["revenue"].fillna(0)
    merged["deals_count"] = merged["deals_count"].fillna(0).astype(int)

    # MQL / SQL (do HubSpot, por lifecycle stage)
    if funnel_df is not None and not funnel_df.empty:
        merged = merged.merge(funnel_df, on="campaign_utm", how="left")
    merged["mqls"] = merged.get("mqls", pd.Series(0, index=merged.index)).fillna(0).astype(int)
    merged["sqls"] = merged.get("sqls", pd.Series(0, index=merged.index)).fillna(0).astype(int)
    # deals_closed = negócios fechados por campanha (do join com HubSpot deals)
    merged["deals_closed"] = merged["deals_count"]

    merged["roas"] = merged.apply(
        lambda r: round(r["revenue"] / r["spend"], 4) if r["spend"] > 0 else 0.0, axis=1
    )
    merged["cpl"] = merged.apply(
        lambda r: round(r["spend"] / r["leads"], 2) if r["leads"] > 0 else 0.0, axis=1
    )
    merged["cpc"] = merged.apply(
        lambda r: round(r["spend"] / r["clicks"], 4) if r["clicks"] > 0 else 0.0, axis=1
    )
    merged["ctr"] = merged.apply(
        lambda r: round(r["clicks"] / r["impressions"], 4) if r["impressions"] > 0 else 0.0, axis=1
    )

    cols = [
        "date", "campaign_utm", "campaign_name", "channel",
        "spend", "impressions", "clicks", "leads", "revenue",
        "roas", "cpl", "cpc", "ctr",
        "mqls", "sqls", "deals_closed",
    ]
    existing_cols = [c for c in cols if c in merged.columns]
    logger.info("Join concluído: %d linhas (diário)", len(merged))
    return merged[existing_cols]
