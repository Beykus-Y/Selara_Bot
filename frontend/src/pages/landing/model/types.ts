export type LandingSignalCard = {
  label: string
  value: string
  note: string
  tone: string
}

export type LandingMetric = {
  label: string
  value: string
  note: string
  tone: string
}

export type LandingStepCard = {
  step: string
  title: string
  text: string
}

export type LandingFeatureCard = {
  kicker: string
  title: string
  text: string
  items: string[]
  href: string | null
  link_label: string
}

export type LandingRouteCard = {
  title: string
  href: string
  display_href: string
  description: string
  note: string
}

export type LandingCta = {
  href: string
  label: string
  variant: string
}

export type LandingPageData = {
  hero_eyebrow: string
  hero_title_primary: string
  hero_title_secondary: string
  hero_subtitle: string
  hero_ctas: LandingCta[]
  session_note: string | null
  developer_credit: string
  signal_cards: LandingSignalCard[]
  metrics: LandingMetric[]
  overview_text: string
  overview_pills: string[]
  step_cards: LandingStepCard[]
  feature_cards: LandingFeatureCard[]
  route_cards: LandingRouteCard[]
}

export type LandingPageResponse =
  | {
      ok: true
      page: LandingPageData
    }
  | {
      ok: false
      message: string
      redirect?: string
    }
