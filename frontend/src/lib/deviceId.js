import axios from "axios";
import { API_URL } from "../config";

// The phone id is issued and signed by the server, so a student can't choose
// or invent one. It is kept in this browser so the server can tell which phone
// a check-in or registration came from.
const KEY = "ft_device_token";

export const getStoredDevice = () => {
  try {
    return localStorage.getItem(KEY) || "";
  } catch {
    return "";
  }
};

const storeDevice = (token) => {
  try {
    localStorage.setItem(KEY, token);
  } catch {
    /* private mode: a new id is issued next time */
  }
};

export const rememberDevice = (token) => {
  if (token && token !== getStoredDevice()) storeDevice(token);
};

// Ask the server to confirm the stored id, or issue a new one.
export const ensureDevice = async () => {
  const existing = getStoredDevice();
  const response = await axios.post(`${API_URL}/device/token`, { device: existing });
  rememberDevice(response.data.device);
  return response.data.device;
};
