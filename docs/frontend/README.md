# Frontend Documentation 📓

## Intro

CZ Gen Epi frontend application is built with the following stack. If you are unfamiliar with any of them, please feel free to check out their documentations in the links below, or reach out to CZI Slack channels **#org-tech-frontend** and **#help-frontend** for help!

1. [TypeScript](https://www.typescriptlang.org/)
1. [React](https://reactjs.org/)
1. [Next.js](https://nextjs.org/)
1. [React-Query](https://react-query.tanstack.com/)
1. [Emotion](http://emotion.sh/)
1. [Material UI](https://material-ui.com/)
1. [czifui](https://github.com/chanzuckerberg/sci-components)
1. [Playwright](https://playwright.dev/)
1. [Redux](https://redux.js.org/)

## App Structure

Given the app is built with [Next.js](https://nextjs.org/), we structure the app following the building blocks required by Next.js. Below you will find a few directories that are important to be familiar with:

1. `frontend/public` - static assets that we need to expose directly through `https://theiagenepi.org/*` live here. For example, <https://theiagenepi.org/robots.txt>

1. `frontend/pages` - All route based pages need their corresponding page files in this folder, this is because Next.js uses a file-system based router.

   For examples:

   1. `./pages/index.tsx` maps to Homepage route: <https://theiagenepi.org/>
   1. `./pages/terms.tsx` maps to Terms of Service route: <https://theiagenepi.org/terms>
   1. `./pages/upload/[[...params]].tsx` maps to **any** Upload route: <https://theiagenepi.org/upload/1>, <https://theiagenepi.org/upload/2>, <https://theiagenepi.org/upload/3>, etc.. Notice that we use double bracket filename here, since this file is a dynamic route page that catches all of its sub-routes. (Learn more about Next.js' dynamic routes [here](https://nextjs.org/docs/routing/dynamic-routes#optional-catch-all-routes))
   1. `./pages/_document.tsx` is used to customize our app's `<html>` and `<body>` tags. In our case, we need to add Material UI's server side rendering code here.
      1. [Material UI server rendering](https://material-ui.com/guides/server-rendering/)
      1. [Material UI server rendering + Next.js example repo](https://github.com/mui-org/material-ui/tree/master/examples/nextjs)
      1. [Customize Next.js `Document`](https://nextjs.org/docs/advanced-features/custom-document)
   1. `./pages/_app.tsx` is used to customize Next.js' `App` component, so we can add global enhancements here. Such as `<ThemeProvider />`, `<QueryClientProvider />`, etc..
      1. [Customize Next.js `App`](https://nextjs.org/docs/advanced-features/custom-app)
   1. Visit [here](https://nextjs.org/docs/routing/introduction) to learn more about Next.js' routing strategy

1. `frontend/src/common` - Anything that's shared globally live here. For examples, api, constants, styles, etc.. **Except for the shared components (see below)**

1. `frontend/src/components` - All shared components live here instead of `./src/common/components`, because `./src/components` is likely to grow a lot bigger and used frequently, so separating it out from `./src/common` as a shortcut to help with browsing and imports

1. `frontend/src/views` - All page files in `./pages` should have their corresponding view component files in this folder, where the implementation details live. For example, `./pages/index.tsx` imports `./views/Homepage` component and renders it like so:

   ```tsx
   import React from "react";
   import Homepage from "src/views/Homepage";

   const Page = (): JSX.Element => <Homepage />;

   export default Page;
   ```

   As you can see, page files are just thin wrappers for the view components. This is because implementation details all live in `frontend/src/*`, and `frontend/pages` is the only odd duck **NOT** under `frontend/src/*`, so we compromise by importing the view components from `frontend/src/views` and use them in `frontend/pages` to render the pages

## React Component Structure

The basic way to build a React component is to follow the steps below:

1. Create a component folder in the directory it belongs to. E.g., a view component lives in `./src/views/*` and a shared component in `./src/components/*`.

   For illustration, we will create a view component named `Foo` in `./src/views/Foo`

1. Create a new file `./src/views/Foo/index.tsx` - this is where the implementation details of `Foo` should live and how the call sites import the component

1. Create `./src/views/Foo/style.ts` to host all styled components you use in `./src/views/Foo/index.tsx`

1. Create `./src/views/Foo/components/` directory to host all sub-components you use in `./src/views/Foo/index.tsx`. For example, if you use component `<Bar />` in `<Foo>`, you can create a directory `./src/views/Foo/components/Bar` to encapsulate `Bar` component's implementation details.

1. And if `Bar` component uses `Baz`, we can create `./src/views/Foo/components/Bar/components/Baz` to encapsulate `Baz` component's implementation details

As you can see, a component is typically made of other components and/or sub-components, so we can use the basic component file structure illustrated above to recursively build out a component at any level. One benefit of this recursive structure is that the component interface and boundaries are well defined, so extracting a component to a different directory is as easy as cut and paste

## Redux Store Structure
All code related to the configuration of the Redux store lives in `./src/common/redux/`.

Here's what you'll find:
- `index.ts` -- store set up, including defining the initial store state on app load.
- `actions/` -- code used to update/write state to the store
- `selectors/` -- code used to read state from the store
- `middleware/` -- code that executes side effects from state updates
- `hooks/` -- pre-typed hooks that you can use in the app without having to do TS declarations every time you use them

Currently, each directory only has one file (`index.ts`) because our store is very small and only holds a couple pieces of state. This dir structure is set up so that we can maintain order as the app grows and potentially add more granular-sized files.

## Data Fetching

CZ Gen Epi uses [React Query](https://react-query.tanstack.com/) and Web API [Fetch](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API) to make API call lifecycle and manage fetched server data. If you want to learn more about React Query and why it's awesome, please check out their [overview](https://react-query.tanstack.com/overview)!

We put all API queries inside `./common/queries`, and depends on the server data type, we further split the code into different files, such as `auth.ts` and `samples.ts`

For GET example, find function `useUserInfo()` in `./src/common/queries/auth.ts` and see how it incorporates `fetch()` and `useQuery`. And global search for `useUserInfo` to see how it's used at the call sites

For POST example, find function `createSamples()` in `./src/common/queries/samples.ts` and see how it incorporates `fetch()`. And global search for `createSamples` to see how the call sites use the POST function along with `useMutation()`. E.g., `./src/views/Upload/components/Review/components/Upload/index.tsx`

## Design System + Component Library ([czifui](https://github.com/chanzuckerberg/sci-components))

CZ Gen Epi uses Science Initiative Design System as the building blocks for composing UI, and `czifui` is the component library counterpart of the design system.

For referencing the design system, please check [Figma](https://www.figma.com/file/EaRifXLFs54XTjO1Mlszkg/Science-Design-System-Reference) and use the left panel to find different types of components (Bases, Genes, DNA, and Chromosomes) ![image](https://user-images.githubusercontent.com/6309723/123888574-a53aec00-d908-11eb-96b3-e32381e30c9a.png)

## Styling

CZ Gen Epi uses [Emotion](https://emotion.sh/), [Material UI](https://material-ui.com/), and Science Initiative component library ([czifui](https://github.com/chanzuckerberg/sci-components)) as the styling solution. This is because `czifui` also uses Emotion and Material UI, so sharing the same stack not only saves us bundle size, but also allows us to use the same styling strategies between the app and the library (less context switching and more code examples), as well as having an easier time to extract components from CZ Gen Epi to the library

Regarding Emotion, we mainly use the `styled()` approach to style components, since this is the recommended way to style components by Material UI (and MUI also uses Emotion)

For more details, visit Emotion's styled component doc [here](https://emotion.sh/docs/styled)

For using `czifui`, please visit the repo [here](https://github.com/chanzuckerberg/sci-components)

### Theming

CZ Gen Epi customizes the default `czifui` theme, in order to have its unique brand identity. As a result, when styling the components in CZ Gen Epi we need to use the custom theme object when writing CSS rules.

For example, throughout the code base, you will find patterns such as the following:

```ts
import { fontBodyM, getColors, getSpaces } from "czifui";

export const Foo = styled.div`
  // This is the design system's font body medium mixin we import from czifui
  ${fontBodyM}

  // This is where the regular css rules go
  overflow: auto;

  // This is a callback function that returns more CSS rules, but the only way
  // to access the custom theme object
  ${(props) => {
    // getColors() is a selector that picks out colors from the theme object
    const colors = getColors(props);
    // getSpaces() is a selector that picks out spaces from the theme object
    const spaces = getSpaces(props);

    return `
      background-color: ${colors?.gray[500]};
      padding-bottom: ${spaces?.m}px;
      margin-bottom: ${spaces?.xxl}px;
    `;
  }}
`;
```

## Feature Flags
[Documented here](https://czi.atlassian.net/wiki/spaces/SCI/pages/2442035223/GenEpi+--+Feature+Flags+Split.io+--+HowTo)

## Gotchas

1. CZ Gen Epi runs the whole stack in different Docker containers (via the `make local-*` commands), including the Frontend.

   However, there are times the FE container and `npm` packages can be out of sync. When it happens, you will experience different runtime errors in the app that **indirectly** suggests the FE container's `node_modules/` directory is out of sync, such as unable to find modules, library does not exist, etc.. When it happens, I typically use the following steps to troubleshoot:

   1. In CZ Gen Epi root directory (not FE root), run `docker-compose exec frontend /bin/bash` to SSH into the FE container

   1. In the FE container terminal, run `ls node_modules` to check the package of interest exists AND has the expected version in the module folder's `package.json`. If not, do the next step

   1. In another FE container terminal, run `npm i`. This will tell the FE container to use the latest `package.json` to update its `node_modules` and `package-lock.json`, so now the FE container should have the correct dependencies. **However**, running `npm i` in the FE container has an unwanted side effect of reverting `package-lock.json` to NPM lock version 1, when we want to use version 2. Thus, please make sure to do the next step

   1. **Important**: In yet another terminal, go to your local CZ Gen Epi's FE directory `aspen/src/frontend/` and run `npm i`. This will update `package-lock.json` again to use lock version 2!

   1. At this point, your app should be working without any runtime error. And if you still have problems, please reach out to CZI Slack channels **#org-tech-frontend** and **#help-frontend** for help!
