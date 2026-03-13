import axios from 'axios'
import { resolveAppPath } from '@/shared/config/app-base-path'

export const http = axios.create({
  baseURL: resolveAppPath('/api'),
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
})
