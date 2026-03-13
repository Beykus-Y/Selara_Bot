export type GamesMetric = {
  label: string
  value: string
  note: string
  tone: string
}

export type GamesSpotlightMetric = {
  label: string
  value: string
}

export type GamesRevealCard = {
  eyebrow: string
  title: string
  text: string
  note: string
}

export type GamesSpotlight = {
  eyebrow: string
  title: string
  description: string
  prompt_title: string | null
  prompt_text: string | null
  metrics: GamesSpotlightMetric[]
  reveal_card?: GamesRevealCard | null
}

export type GamesButton = {
  kind: string
  label: string
  callback_data?: string
  url?: string
  variant: string
}

export type GamesCategoryOption = {
  value: string
  label: string
  note: string
  count: string
  is_18_plus?: boolean
}

export type GamesThemePicker = {
  game_id: string
  current_value: string
  current_label: string
  options: GamesCategoryOption[]
}

export type GamesBadge = {
  label: string
  tone: string
}

export type GamesRoleCard = {
  title: string
  tone: string
  team: string
  intel_label?: string
  intel_value?: string
  objective: string
  ability: string
}

export type GamesScoreRow = {
  position: string
  label: string
  value: string
}

export type GamesSubmissionRow = {
  label: string
  state: string
  state_label: string
}

export type GamesRevealRow = {
  slot: string
  text: string
  author?: string
  votes: string
  is_truth?: boolean
  is_winner?: boolean
  tone: string
}

export type GamesWhoamiTableRow = {
  label: string
  identity: string
  tone: string
  badges: GamesBadge[]
  title: string
}

export type GamesWhoamiHistoryRow = {
  title: string
  text: string
  meta: string
  tone: string
}

export type GamesSpySuspectRow = {
  label: string
  votes: string
  meter: string
  tone: string
  badges: GamesBadge[]
}

export type GamesSpySummary = {
  category: string
  votes: string
  majority: string
  leader: string
}

export type GamesMafiaRosterRow = {
  label: string
  tone: string
  badges: GamesBadge[]
}

export type GamesRoleRevealRow = {
  player: string
  role: string
  team: string
  tone: string
  winner: boolean
}

export type GamesSpyView = {
  role_card: GamesRoleCard
  suspect_rows: GamesSpySuspectRow[]
  summary: GamesSpySummary
  status_title: string
  status_text: string
  status_tone: string
  action_title: string
  action_text: string
  action_buttons: GamesButton[]
  guess_form: {
    game_id: string
    locations: string[]
    placeholder: string
    button_label: string
  } | null
}

export type GamesWhoamiView = {
  category: string
  current_actor: string
  pending_question: string | null
  solved_count: string
  players_total: string
  table_rows: GamesWhoamiTableRow[]
  history_rows: GamesWhoamiHistoryRow[]
  status_title: string
  status_text: string
  status_tone: string
  action_buttons: GamesButton[]
  question_form: {
    game_id: string
    placeholder: string
    button_label: string
  } | null
  guess_form: {
    game_id: string
    placeholder: string
    button_label: string
  } | null
}

export type GamesMafiaView = {
  role_card: GamesRoleCard
  report_html: string | null
  roster_rows: GamesMafiaRosterRow[]
  status_title: string
  status_text: string
  status_tone: string
  action_title: string
  action_text: string
  action_buttons: GamesButton[]
}

export type GamesZlobView = {
  category: string
  round_label: string
  target_score: string
  black_text: string
  black_slots: number
  status_title: string
  status_text: string
  status_tone: string
  submit_buttons: GamesButton[]
  vote_buttons: GamesButton[]
  submit_form: {
    game_id: string
    slots: number
    hand: Array<{ index: string; text: string }>
  } | null
  submission_rows: GamesSubmissionRow[]
  option_rows: GamesRevealRow[]
  voted_option_label: string | null
  show_vote: boolean
}

export type GamesCard = {
  game_id: string
  chat_id: string
  chat_title: string
  title: string
  kind: string
  status: string
  status_badge: string
  description: string
  players_count: number
  round_no: string
  created_at: string
  started_at: string
  is_member: boolean
  is_owner: boolean
  can_manage_games: boolean
  players_preview: string[]
  players_hidden: number
  winner_text: string | null
  spotlight: GamesSpotlight | null
  main_buttons: GamesButton[]
  manage_buttons: GamesButton[]
  category_buttons: GamesButton[]
  vote_buttons: GamesButton[]
  telegram_buttons: GamesButton[]
  private_buttons: GamesButton[]
  spy_theme_picker: GamesThemePicker | null
  whoami_theme_picker: GamesThemePicker | null
  zlob_theme_picker: GamesThemePicker | null
  show_number_guess: boolean
  show_bred_answer: boolean
  bred_submission_rows: GamesSubmissionRow[]
  bred_reveal_rows: GamesRevealRow[]
  spy_view: GamesSpyView | null
  whoami_view: GamesWhoamiView | null
  mafia_view: GamesMafiaView | null
  zlob_view: GamesZlobView | null
  role_reveal_rows: GamesRoleRevealRow[]
  role_reveal_note: string
  secret_lines: string[]
  score_rows: GamesScoreRow[]
}

export type GamesRecentCard = {
  game_id: string
  kind: string
  title: string
  chat_title: string
  chat_id: string
  started_at: string
  result_text: string
  personal_notes: string[]
  score_rows: GamesScoreRow[]
  role_reveal_rows: GamesRoleRevealRow[]
  role_reveal_note: string
  bred_reveal_rows: GamesRevealRow[]
}

export type GamesCatalogItem = {
  key: string
  title: string
  description: string
  min_players_label: string
  mode_label: string
  note: string
  tone: string
}

export type GamesPageData = {
  hero_title: string
  hero_subtitle: string
  metrics: GamesMetric[]
  game_cards: GamesCard[]
  recent_game_cards: GamesRecentCard[]
  game_catalog: GamesCatalogItem[]
  spy_category_options: GamesCategoryOption[]
  whoami_category_options: GamesCategoryOption[]
  zlob_category_options: GamesCategoryOption[]
  default_create_kind: string
  default_create_game: GamesCatalogItem | null
  create_chat_options: Array<{ chat_id: string; title: string; actions_18_enabled: string }>
  busy_create_chat_options: Array<{ chat_id: string; title: string; actions_18_enabled: string }>
  has_manageable_chats: boolean
}

export type GamesPageSuccessResponse = {
  ok: true
  page: GamesPageData
}

export type GamesPageErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type GamesPageResponse = GamesPageSuccessResponse | GamesPageErrorResponse
