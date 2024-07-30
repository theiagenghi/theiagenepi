import { createTheme } from "@mui/material/styles";
import { defaultAppTheme, makeThemeOptions } from "czifui";

const primaryColors = {
  "100": "#A8E2FF",
  "200": "#88C4FF",
  "300": "#67A6F5",
  "400": "#438AD6",
  "500": "#116EB7",
  "600": "#001A54",
};

const infoColors = {
  "100": "#A8E2FF",
  "200": "#67A6F5",
  "400": "#116EB7",
  "600": "#001A54",
};

const primaryBorders = {
  300: `1px solid ${primaryColors[300]}`,
  400: `1px solid ${primaryColors[400]}`,
  500: `1px solid ${primaryColors[500]}`,
  600: `1px solid ${primaryColors[600]}`,
  dashed: `2px dashed ${primaryColors[400]}`,
};

const appTheme = { ...defaultAppTheme };

appTheme.colors.primary = primaryColors;
appTheme.colors.info = infoColors;

appTheme.borders = appTheme.borders ?? {
  error: {},
  gray: {},
  link: {},
  primary: {},
  success: {},
  warning: {},
};
appTheme.borders.primary = primaryBorders;

export const theme = createTheme(makeThemeOptions(appTheme));
