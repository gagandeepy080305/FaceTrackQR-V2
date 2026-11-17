import axios from "axios";
import { API_URL } from "../config";

export const TOKEN_KEY = "faculty_token";
export const FACULTY_KEY = "faculty_id";

export const clearFacultySession = () => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(FACULTY_KEY);
};

/**
 * Axios instance for every faculty-only endpoint. It attaches the login token
 * and, if the server says the token is missing/expired, signs the user out
 * and returns them to the login page.
 */
export const facultyApi = axios.create({ baseURL: API_URL });

facultyApi.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

facultyApi.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && localStorage.getItem(TOKEN_KEY)) {
      clearFacultySession();
      window.location.assign("/?expired=1");
    }
    return Promise.reject(error);
  }
);
