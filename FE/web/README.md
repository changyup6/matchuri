# 맛추리

취향에 맞는 식당을 탐색하고 저장할 수 있는 맛집 추천 서비스입니다.

## 기술 스택

- Next.js 16
- React 19
- TypeScript
- Tailwind CSS **v4**
- TanStack Query (React Query)
- ESLint
- Prettier
- npm

> Tailwind는 v4입니다. v3와 설정 방식이 달라 `tailwind.config.ts` 파일을 사용하지 않습니다.

---

## Getting Started

### 1. Repository Clone

```bash
git clone https://github.com/OWNER/matchuri.git
cd matchuri/FE/web
```

### 2. Node Version

**Node 24.19.0**을 사용합니다. (`.nvmrc`에 명시)

nvm / fnm 등 버전 관리 도구를 사용한다면:

```bash
nvm use
```

도구를 사용하지 않는다면 설치된 버전이 맞는지만 확인합니다.

```bash
node -v
```

> Windows의 nvm-windows는 `.nvmrc` 지원이 제한적입니다.
> 버전이 다르면 `nvm install 24.19.0` 후 `nvm use 24.19.0`으로 직접 지정하세요.

### 3. Package Install

```bash
npm ci
```

### 4. Development Server

```bash
npm run dev
```

실행 후 아래 주소에서 확인할 수 있습니다.

```text
http://localhost:3000
```

---

## Scripts

| Script                 | 설명                     |
| ---------------------- | ------------------------ |
| `npm run dev`          | 개발 서버 실행           |
| `npm run build`        | Production Build         |
| `npm run start`        | Production Build 실행    |
| `npm run lint`         | ESLint 검사              |
| `npm run format`       | Prettier Formatting      |
| `npm run format:check` | Prettier Formatting 검사 |

---

## 프로젝트 구조

현재 구조입니다.

```text
src/
├─ app/          # Next.js Routing / Page / Layout / Global CSS
├─ components/   # UI Component
└─ fonts/        # Local Font (Paperlogy)
```

---

상세한 규칙과 배경 설명은 팀 노션 문서를 참고

- [Frontend 개발 문서](https://elfin-roof-54e.notion.site/Frontend-3cfbf451935e80d1a0e0c9c6fd75ca83?source=copy_link)
