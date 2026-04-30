import logging
import pandas as pd

logger = logging.getLogger(__name__)


def join_campaign_data(ads_df: pd.DataFrame, deals_df: pd.DataFrame) -> pd.DataFrame:
    """
    Join de gastos de Ads com negócios do HubSpot por utm_campaign.
    Retorna DataFrame consolidado com uma linha por campanha.
    """
    if ads_df.empty:
        logger.warning("ads_df vazio — retornando DataFrame vazio")
        return pd.DataFrame()

    ads_agg = (
        ads_df.groupby("campaign_utm", as_index=False)
        .agg(
            channel=("channel", "first"),
            campaign_name=("campaign_name", "first"),
            spend=("spend", "sum"),
            impressions=("impressions", "sum"),
            clicks=("clicks", "sum"),
            leads=("leads", "sum"),
        )
    )

    if not deals_df.empty and "utm_campaign_origem" in deals_df.columns:
        deals_agg = (
            deals_df.groupby("utm_campaign_origem", as_index=False)
            .agg(
                revenue=("amount", "sum"),
                deals_count=("id", "count"),
            )
            .rename(columns={"utm_campaign_origem": "campaign_utm"})
        )
        merged = ads_agg.merge(deals_agg, on="campaign_utm", how="left")
    else:
        merged = ads_agg.copy()
        merged["revenue"] = 0.0
        merged["deals_count"] = 0

    merged["revenue"] = merged["revenue"].fillna(0)
    merged["deals_count"] = merged["deals_count"].fillna(0).astype(int)

    merged["roas"] = merged.apply(
        lambda r: round(r["revenue"] / r["spend"], 4) if r["spend"] > 0 else 0.0, axis=1
    )
    merged["cac"] = merged.apply(
        lambda r: round(r["spend"] / r["deals_count"], 2) if r["deals_count"] > 0 else 0.0, axis=1
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

    merged["produto"] = merged["campaign_utm"].apply(
        lambda x: x.split("_")[0] if "_" in str(x) else x
    )

    cols = [
        "campaign_utm", "campaign_name", "channel", "produto",
        "spend", "impressions", "clicks", "leads", "revenue", "deals_count",
        "roas", "cac", "cpl", "cpc", "ctr",
    ]
    logger.info("Join concluído: %d campanhas", len(merged))
    return merged[cols]
