import { useMutation, useQueryClient } from '@tanstack/react-query'

import { updatePersonalModel } from '@/pages/personal/api/personal-api'
import type { PersonalOverview } from '@/pages/personal/model/types'

export function ModelSection({ data }: { data: PersonalOverview }) {
  const queryClient = useQueryClient()
  const { model } = data
  const choose = useMutation({
    mutationFn: updatePersonalModel,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['miniapp-personal'] }),
  })

  return (
    <section className="miniapp-section-card personal-model" aria-labelledby="personal-model-title">
      <div className="miniapp-section-head">
        <h2 id="personal-model-title">Модель ответа</h2>
      </div>
      {model.selectable ? (
        <p className="selara-ai__note">
          Каждый запрос списывает из суточного бюджета столько AI Limits, сколько стоит модель. Сейчас: {model.effective_name}, {model.cost_ail} AIL за запрос.
        </p>
      ) : (
        <p className="selara-ai__note">
          Сейчас каждый запрос считается как один из суточного лимита, и отвечает базовая модель. Выбор моделей станет
          доступен после включения AI Limits.
        </p>
      )}
      {model.fell_back && (
        <p className="personal-notice is-error" role="status">
          Выбранный профиль сейчас недоступен, используется {model.effective_name} модель.
        </p>
      )}
      <ul className="personal-model__list">
        {model.options.map((option) => {
          const selected = option.profile_key === model.selected
          const disabled = !model.selectable || !option.available || choose.isPending
          return (
            <li key={option.profile_key}>
              <button
                className={`personal-model__option${selected ? ' is-selected' : ''}`}
                type="button"
                aria-pressed={selected}
                disabled={disabled}
                onClick={() => choose.mutate(option.profile_key)}
              >
                <span className="personal-model__name">
                  {option.emoji} {option.display_name}
                </span>
                <span className="personal-model__cost">×{option.ail_multiplier} AIL</span>
                <small>{option.available ? option.description : 'Сейчас недоступна'}</small>
              </button>
            </li>
          )
        })}
      </ul>
      {choose.isError && (
        <p className="personal-notice is-error" role="alert">
          {choose.error.message}
        </p>
      )}
    </section>
  )
}
