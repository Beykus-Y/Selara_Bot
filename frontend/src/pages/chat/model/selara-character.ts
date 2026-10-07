export type SelaraCallName = {
  display: string
  norm: string
  is_primary: boolean
  active: boolean
}

export type SelaraChatSettings = {
  chat_id: number
  can_manage: boolean
  paid: boolean
  names: SelaraCallName[]
  name_limit: number
  character: {
    preset: string
    custom: string | null
    custom_key: string
    presets: Array<{ key: string; title: string }>
  }
  member_mode: boolean
  history: boolean
  actions: boolean
  limits: { daily: number; per_actor: number }
}

export type SelaraAction =
  | 'add_name'
  | 'remove_name'
  | 'set_primary'
  | 'set_preset'
  | 'set_custom'
  | 'member_mode'
  | 'history'
  | 'actions'
  | 'reset_history'
