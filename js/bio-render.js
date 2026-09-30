// Renders a link-in-bio page from its config. Used by /link-<handle> and by the
// editor's live preview, so what the creator sees is what fans get.
(function () {
  // Platforms for the social row. Logos are Simple Icons (CC0); Fanvue, Fansly,
  // Throne and the generic ones are drawn here because Simple Icons has none.
  var NETS = {"instagram":{"label":"Instagram","url":"https://instagram.com/{u}","kind":"user","path":"M7.0301.084c-1.2768.0602-2.1487.264-2.911.5634-.7888.3075-1.4575.72-2.1228 1.3877-.6652.6677-1.075 1.3368-1.3802 2.127-.2954.7638-.4956 1.6365-.552 2.914-.0564 1.2775-.0689 1.6882-.0626 4.947.0062 3.2586.0206 3.6671.0825 4.9473.061 1.2765.264 2.1482.5635 2.9107.308.7889.72 1.4573 1.388 2.1228.6679.6655 1.3365 1.0743 2.1285 1.38.7632.295 1.6361.4961 2.9134.552 1.2773.056 1.6884.069 4.9462.0627 3.2578-.0062 3.668-.0207 4.9478-.0814 1.28-.0607 2.147-.2652 2.9098-.5633.7889-.3086 1.4578-.72 2.1228-1.3881.665-.6682 1.0745-1.3378 1.3795-2.1284.2957-.7632.4966-1.636.552-2.9124.056-1.2809.0692-1.6898.063-4.948-.0063-3.2583-.021-3.6668-.0817-4.9465-.0607-1.2797-.264-2.1487-.5633-2.9117-.3084-.7889-.72-1.4568-1.3876-2.1228C21.2982 1.33 20.628.9208 19.8378.6165 19.074.321 18.2017.1197 16.9244.0645 15.6471.0093 15.236-.005 11.977.0014 8.718.0076 8.31.0215 7.0301.0839m.1402 21.6932c-1.17-.0509-1.8053-.2453-2.2287-.408-.5606-.216-.96-.4771-1.3819-.895-.422-.4178-.6811-.8186-.9-1.378-.1644-.4234-.3624-1.058-.4171-2.228-.0595-1.2645-.072-1.6442-.079-4.848-.007-3.2037.0053-3.583.0607-4.848.05-1.169.2456-1.805.408-2.2282.216-.5613.4762-.96.895-1.3816.4188-.4217.8184-.6814 1.3783-.9003.423-.1651 1.0575-.3614 2.227-.4171 1.2655-.06 1.6447-.072 4.848-.079 3.2033-.007 3.5835.005 4.8495.0608 1.169.0508 1.8053.2445 2.228.408.5608.216.96.4754 1.3816.895.4217.4194.6816.8176.9005 1.3787.1653.4217.3617 1.056.4169 2.2263.0602 1.2655.0739 1.645.0796 4.848.0058 3.203-.0055 3.5834-.061 4.848-.051 1.17-.245 1.8055-.408 2.2294-.216.5604-.4763.96-.8954 1.3814-.419.4215-.8181.6811-1.3783.9-.4224.1649-1.0577.3617-2.2262.4174-1.2656.0595-1.6448.072-4.8493.079-3.2045.007-3.5825-.006-4.848-.0608M16.953 5.5864A1.44 1.44 0 1 0 18.39 4.144a1.44 1.44 0 0 0-1.437 1.4424M5.8385 12.012c.0067 3.4032 2.7706 6.1557 6.173 6.1493 3.4026-.0065 6.157-2.7701 6.1506-6.1733-.0065-3.4032-2.771-6.1565-6.174-6.1498-3.403.0067-6.156 2.771-6.1496 6.1738M8 12.0077a4 4 0 1 1 4.008 3.9921A3.9996 3.9996 0 0 1 8 12.0077","color":"#FF0069"},"tiktok":{"label":"TikTok","url":"https://tiktok.com/@{u}","kind":"user","path":"M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z","color":"#000000"},"x":{"label":"X","url":"https://x.com/{u}","kind":"user","path":"M14.234 10.162 22.977 0h-2.072l-7.591 8.824L7.251 0H.258l9.168 13.343L.258 24H2.33l8.016-9.318L16.749 24h6.993zm-2.837 3.299-.929-1.329L3.076 1.56h3.182l5.965 8.532.929 1.329 7.754 11.09h-3.182z","color":"#000000"},"onlyfans":{"label":"OnlyFans","url":"https://onlyfans.com/{u}","kind":"user","path":"M24 4.003h-4.015c-3.45 0-5.3.197-6.748 1.957a7.996 7.996 0 1 0 2.103 9.211c3.182-.231 5.39-2.134 6.085-5.173 0 0-2.399.585-4.43 0 4.018-.777 6.333-3.037 7.005-5.995zM5.61 11.999A2.391 2.391 0 0 1 9.28 9.97a2.966 2.966 0 0 1 2.998-2.528h.008c-.92 1.778-1.407 3.352-1.998 5.263A2.392 2.392 0 0 1 5.61 12Zm2.386-7.996a7.996 7.996 0 1 0 7.996 7.996 7.996 7.996 0 0 0-7.996-7.996Zm0 10.394A2.399 2.399 0 1 1 10.395 12a2.396 2.396 0 0 1-2.399 2.398Z","color":"#00AFF0"},"fanvue":{"label":"Fanvue","url":"https://fanvue.com/{u}","kind":"user","color":"#1A1A1A","svg":"<path d=\"M7 4h11M7 4v16M7 11h9\" stroke=\"currentColor\" stroke-width=\"2.8\" stroke-linecap=\"round\" fill=\"none\"/>"},"fansly":{"label":"Fansly","url":"https://fansly.com/{u}","kind":"user","color":"#1FA7F8","svg":"<path d=\"M12 21s-8-5-8-11a4.5 4.5 0 0 1 8-2.8A4.5 4.5 0 0 1 20 10c0 6-8 11-8 11z\" fill=\"currentColor\"/>"},"telegram":{"label":"Telegram","url":"https://t.me/{u}","kind":"user","path":"M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z","color":"#26A5E4"},"snapchat":{"label":"Snapchat","url":"https://snapchat.com/add/{u}","kind":"user","path":"M12.206.793c.99 0 4.347.276 5.93 3.821.529 1.193.403 3.219.299 4.847l-.003.06c-.012.18-.022.345-.03.51.075.045.203.09.401.09.3-.016.659-.12 1.033-.301.165-.088.344-.104.464-.104.182 0 .359.029.509.09.45.149.734.479.734.838.015.449-.39.839-1.213 1.168-.089.029-.209.075-.344.119-.45.135-1.139.36-1.333.81-.09.224-.061.524.12.868l.015.015c.06.136 1.526 3.475 4.791 4.014.255.044.435.27.42.509 0 .075-.015.149-.045.225-.24.569-1.273.988-3.146 1.271-.059.091-.12.375-.164.57-.029.179-.074.36-.134.553-.076.271-.27.405-.555.405h-.03c-.135 0-.313-.031-.538-.074-.36-.075-.765-.135-1.273-.135-.3 0-.599.015-.913.074-.6.104-1.123.464-1.723.884-.853.599-1.826 1.288-3.294 1.288-.06 0-.119-.015-.18-.015h-.149c-1.468 0-2.427-.675-3.279-1.288-.599-.42-1.107-.779-1.707-.884-.314-.045-.629-.074-.928-.074-.54 0-.958.089-1.272.149-.211.043-.391.074-.54.074-.374 0-.523-.224-.583-.42-.061-.192-.09-.389-.135-.567-.046-.181-.105-.494-.166-.57-1.918-.222-2.95-.642-3.189-1.226-.031-.063-.052-.15-.055-.225-.015-.243.165-.465.42-.509 3.264-.54 4.73-3.879 4.791-4.02l.016-.029c.18-.345.224-.645.119-.869-.195-.434-.884-.658-1.332-.809-.121-.029-.24-.074-.346-.119-1.107-.435-1.257-.93-1.197-1.273.09-.479.674-.793 1.168-.793.146 0 .27.029.383.074.42.194.789.3 1.104.3.234 0 .384-.06.465-.105l-.046-.569c-.098-1.626-.225-3.651.307-4.837C7.392 1.077 10.739.807 11.727.807l.419-.015h.06z","color":"#FFFC00"},"reddit":{"label":"Reddit","url":"https://reddit.com/user/{u}","kind":"user","path":"M12 0C5.373 0 0 5.373 0 12c0 3.314 1.343 6.314 3.515 8.485l-2.286 2.286C.775 23.225 1.097 24 1.738 24H12c6.627 0 12-5.373 12-12S18.627 0 12 0Zm4.388 3.199c1.104 0 1.999.895 1.999 1.999 0 1.105-.895 2-1.999 2-.946 0-1.739-.657-1.947-1.539v.002c-1.147.162-2.032 1.15-2.032 2.341v.007c1.776.067 3.4.567 4.686 1.363.473-.363 1.064-.58 1.707-.58 1.547 0 2.802 1.254 2.802 2.802 0 1.117-.655 2.081-1.601 2.531-.088 3.256-3.637 5.876-7.997 5.876-4.361 0-7.905-2.617-7.998-5.87-.954-.447-1.614-1.415-1.614-2.538 0-1.548 1.255-2.802 2.803-2.802.645 0 1.239.218 1.712.585 1.275-.79 2.881-1.291 4.64-1.365v-.01c0-1.663 1.263-3.034 2.88-3.207.188-.911.993-1.595 1.959-1.595Zm-8.085 8.376c-.784 0-1.459.78-1.506 1.797-.047 1.016.64 1.429 1.426 1.429.786 0 1.371-.369 1.418-1.385.047-1.017-.553-1.841-1.338-1.841Zm7.406 0c-.786 0-1.385.824-1.338 1.841.047 1.017.634 1.385 1.418 1.385.785 0 1.473-.413 1.426-1.429-.046-1.017-.721-1.797-1.506-1.797Zm-3.703 4.013c-.974 0-1.907.048-2.77.135-.147.015-.241.168-.183.305.483 1.154 1.622 1.964 2.953 1.964 1.33 0 2.47-.81 2.953-1.964.057-.137-.037-.29-.184-.305-.863-.087-1.795-.135-2.769-.135Z","color":"#FF4500"},"threads":{"label":"Threads","url":"https://threads.net/@{u}","kind":"user","path":"M18.263 11.097c-.03-3.486-1.92-5.586-5.111-5.586-2.13 0-3.922.963-4.863 2.499l2.062 1.438c.535-.843 1.272-1.543 2.628-1.543 1.528 0 2.318.85 2.544 2.431a15 15 0 0 0-2.236-.173c-4.125 0-6.068 1.867-6.068 4.336s1.943 3.99 4.804 3.99c3.139 0 5.013-2.115 5.781-4.735.798.361 1.348 1.204 1.348 2.47 0 3.387-3.907 5.232-7.22 5.232-4.885 0-8.077-3.207-8.077-8.424 0-6.392 4.223-10.487 9.9-10.487 3.808 0 5.69 1.671 6.97 3.914l2.108-1.475C21.44 2.078 18.331 0 13.663 0 6.227 0 1.168 5.277 1.168 12.934c0 7 4.953 11.066 10.856 11.066 4.878 0 9.809-2.846 9.809-7.716 0-2.545-1.46-4.231-3.569-5.187m-6.33 4.855c-1.077 0-2.026-.512-2.026-1.453 0-1.483 1.822-1.934 3.606-1.934.678 0 1.34.045 1.927.173-.422 1.927-1.671 3.215-3.508 3.214Z","color":"#000000"},"youtube":{"label":"YouTube","url":"https://youtube.com/@{u}","kind":"user","path":"M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z","color":"#FF0000"},"twitch":{"label":"Twitch","url":"https://twitch.tv/{u}","kind":"user","path":"M11.571 4.714h1.715v5.143H11.57zm4.715 0H18v5.143h-1.714zM6 0L1.714 4.286v15.428h5.143V24l4.286-4.286h3.428L22.286 12V0zm14.571 11.143l-3.428 3.428h-3.429l-3 3v-3H6.857V1.714h13.714Z","color":"#9146FF"},"kick":{"label":"Kick","url":"https://kick.com/{u}","kind":"user","path":"M1.333 0h8v5.333H12V2.667h2.667V0h8v8H20v2.667h-2.667v2.666H20V16h2.667v8h-8v-2.667H12v-2.666H9.333V24h-8Z","color":"#53FC19"},"facebook":{"label":"Facebook","url":"https://facebook.com/{u}","kind":"user","path":"M9.101 23.691v-7.98H6.627v-3.667h2.474v-1.58c0-4.085 1.848-5.978 5.858-5.978.401 0 .955.042 1.468.103a8.68 8.68 0 0 1 1.141.195v3.325a8.623 8.623 0 0 0-.653-.036 26.805 26.805 0 0 0-.733-.009c-.707 0-1.259.096-1.675.309a1.686 1.686 0 0 0-.679.622c-.258.42-.374.995-.374 1.752v1.297h3.919l-.386 2.103-.287 1.564h-3.246v8.245C19.396 23.238 24 18.179 24 12.044c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.628 3.874 10.35 9.101 11.647Z","color":"#0866FF"},"pinterest":{"label":"Pinterest","url":"https://pinterest.com/{u}","kind":"user","path":"M12.017 0C5.396 0 .029 5.367.029 11.987c0 5.079 3.158 9.417 7.618 11.162-.105-.949-.199-2.403.041-3.439.219-.937 1.406-5.957 1.406-5.957s-.359-.72-.359-1.781c0-1.663.967-2.911 2.168-2.911 1.024 0 1.518.769 1.518 1.688 0 1.029-.653 2.567-.992 3.992-.285 1.193.6 2.165 1.775 2.165 2.128 0 3.768-2.245 3.768-5.487 0-2.861-2.063-4.869-5.008-4.869-3.41 0-5.409 2.562-5.409 5.199 0 1.033.394 2.143.889 2.741.099.12.112.225.085.345-.09.375-.293 1.199-.334 1.363-.053.225-.172.271-.401.165-1.495-.69-2.433-2.878-2.433-4.646 0-3.776 2.748-7.252 7.92-7.252 4.158 0 7.392 2.967 7.392 6.923 0 4.135-2.607 7.462-6.233 7.462-1.214 0-2.354-.629-2.758-1.379l-.749 2.848c-.269 1.045-1.004 2.352-1.498 3.146 1.123.345 2.306.535 3.55.535 6.607 0 11.985-5.365 11.985-11.987C23.97 5.39 18.592.026 11.985.026L12.017 0z","color":"#BD081C"},"bluesky":{"label":"Bluesky","url":"https://bsky.app/profile/{u}","kind":"user","path":"M5.202 2.857C7.954 4.922 10.913 9.11 12 11.358c1.087-2.247 4.046-6.436 6.798-8.501C20.783 1.366 24 .213 24 3.883c0 .732-.42 6.156-.667 7.037-.856 3.061-3.978 3.842-6.755 3.37 4.854.826 6.089 3.562 3.422 6.299-5.065 5.196-7.28-1.304-7.847-2.97-.104-.305-.152-.448-.153-.327 0-.121-.05.022-.153.327-.568 1.666-2.782 8.166-7.847 2.97-2.667-2.737-1.432-5.473 3.422-6.3-2.777.473-5.899-.308-6.755-3.369C.42 10.04 0 4.615 0 3.883c0-3.67 3.217-2.517 5.202-1.026","color":"#1185FE"},"patreon":{"label":"Patreon","url":"https://patreon.com/{u}","kind":"user","path":"M22.957 7.21c-.004-3.064-2.391-5.576-5.191-6.482-3.478-1.125-8.064-.962-11.384.604C2.357 3.231 1.093 7.391 1.046 11.54c-.039 3.411.302 12.396 5.369 12.46 3.765.047 4.326-4.804 6.068-7.141 1.24-1.662 2.836-2.132 4.801-2.618 3.376-.836 5.678-3.501 5.673-7.031Z","color":"#000000"},"throne":{"label":"Throne","url":"https://throne.com/{u}","kind":"user","color":"#6C47FF","svg":"<path d=\"M3 8l4.5 4L12 5l4.5 7L21 8l-2 11H5z\" fill=\"currentColor\"/>"},"discord":{"label":"Discord","url":"https://discord.gg/{u}","kind":"user","path":"M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z","color":"#5865F2"},"whatsapp":{"label":"WhatsApp","url":"https://wa.me/{u}","kind":"phone","path":"M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413Z","color":"#25D366"},"spotify":{"label":"Spotify","url":"","kind":"url","path":"M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z","color":"#1ED760"},"amazon":{"label":"Amazon wishlist","url":"","kind":"url","color":"#FF9900","svg":"<rect x=\"3\" y=\"9\" width=\"18\" height=\"4\" rx=\"1\" fill=\"currentColor\"/><rect x=\"5\" y=\"13\" width=\"14\" height=\"8\" rx=\"1\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"/><path d=\"M12 9v12M12 9c-1.5-4-6-4-5-1.5.6 1.4 5 1.5 5 1.5zm0 0c1.5-4 6-4 5-1.5-.6 1.4-5 1.5-5 1.5z\" stroke=\"currentColor\" stroke-width=\"1.8\" fill=\"none\"/>"},"email":{"label":"Email","url":"mailto:{u}","kind":"email","color":"#6B7280","svg":"<rect x=\"3\" y=\"5\" width=\"18\" height=\"14\" rx=\"2.5\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"/><path d=\"M4 7l8 6 8-6\" stroke=\"currentColor\" stroke-width=\"2\" fill=\"none\"/>"},"website":{"label":"Website","url":"","kind":"url","color":"#6B7280","svg":"<circle cx=\"12\" cy=\"12\" r=\"9\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"/><path d=\"M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18\" stroke=\"currentColor\" stroke-width=\"1.8\" fill=\"none\"/>"}};
  function lum(hex) {
    var n = parseInt(hex.slice(1), 16);
    return (0.299 * (n >> 16) + 0.587 * (n >> 8 & 255) + 0.114 * (n & 255)) / 255;
  }
  function icon(net) {
    var n = NETS[net];
    if (!n) return '';
    return '<svg viewBox="0 0 24 24" aria-hidden="true">' + (n.svg || '<path fill="currentColor" d="' + n.path + '"/>') + '</svg>';
  }
  function netOf(url) {
    var host = '';
    try { host = new URL(url).hostname.replace(/^www\./, ''); } catch (e) { return ''; }
    var alias = { 't.me': 'telegram', 'wa.me': 'whatsapp', 'bsky.app': 'bluesky', 'discord.gg': 'discord', 'twitter.com': 'x', 'youtu.be': 'youtube', 'open.spotify.com': 'spotify' };
    if (alias[host]) return alias[host];
    if (/(^|\.)amazon\./.test(host)) return 'amazon';
    var base = host.split('.').slice(-2, -1)[0];
    return NETS[base] && NETS[base].url ? base : '';
  }
  function badge(net, brand) {
    var n = NETS[net];
    if (!brand) return icon(net);
    return '<span class="bio-badge" style="background:' + n.color + ';color:' + (lum(n.color) > 0.62 ? '#111' : '#fff') + '">' + icon(net) + '</span>';
  }
  window.BIO_NETS = NETS;
  window.bioIcon = icon;
  window.bioNetOf = netOf;
  var CSS = '' +
    '.bio{min-height:100%;box-sizing:border-box;display:flex;flex-direction:column;align-items:center;padding:48px 20px 28px;position:relative;background-size:cover;background-position:center}' +
    '.bio *{box-sizing:border-box}' +
    '.bio-in{width:100%;max-width:560px;display:flex;flex-direction:column;align-items:center;gap:12px;flex:1}' +
    '.bio-av{width:96px;height:96px;object-fit:cover;flex:none;background:rgba(0,0,0,.08)}' +
    '.bio-av.circle{border-radius:50%}.bio-av.rounded{border-radius:28px}.bio-av.cover{width:100%;height:220px;border-radius:var(--bio-r)}' +
    '.bio-name{margin:4px 0 0;font-size:30px;line-height:1.1;text-align:center;word-break:break-word}' +
    '.bio-text{margin:0;text-align:center;font-size:15px;max-width:40ch;opacity:.85;white-space:pre-line}' +
    '.bio-soc{display:flex;flex-wrap:wrap;gap:16px;justify-content:center;margin:2px 0 6px}' +
    '.bio-soc a{color:inherit;display:flex;opacity:.9}.bio-soc a:hover{opacity:1;transform:translateY(-1px)}.bio-soc svg{width:24px;height:24px}' +
    '.bio-badge{width:38px;height:38px;border-radius:50%;display:flex;align-items:center;justify-content:center}.bio-soc .bio-badge svg{width:20px;height:20px}' +
    '.bio-bic{position:absolute;left:16px;top:50%;transform:translateY(-50%);display:flex}.bio-bic svg{width:24px;height:24px}' +
    '.bio-btn{width:100%;min-height:56px;display:flex;align-items:center;justify-content:center;gap:10px;padding:8px 52px;position:relative;text-decoration:none;font-weight:600;font-size:15.5px;text-align:center;border-radius:var(--bio-r);cursor:pointer;transition:transform .15s;border:0;font-family:inherit;background:var(--bio-bb);color:var(--bio-bt)}' +
    '.bio-btn:hover{transform:scale(1.015)}.bio-btn:focus-visible{outline:3px solid var(--bio-ac);outline-offset:3px}' +
    '.bio-btn img{position:absolute;left:8px;top:50%;transform:translateY(-50%);width:40px;height:40px;object-fit:cover;border-radius:calc(var(--bio-r) * .7)}' +
    '.bio-btn .tag{position:absolute;right:16px;font-size:11px;font-weight:700;opacity:.7}' +
    '.st-glass .bio-btn{background:color-mix(in srgb,var(--bio-bb) 18%,transparent);border:1px solid color-mix(in srgb,var(--bio-bt) 22%,transparent);backdrop-filter:blur(8px);color:var(--bio-bt)}' +
    '.st-outline .bio-btn{background:transparent;border:2px solid var(--bio-bt)}' +
    '.st-shadow .bio-btn{border:2.5px solid var(--bio-tx);box-shadow:5px 5px 0 var(--bio-tx)}' +
    '.st-fill .bio-btn{box-shadow:0 6px 16px -10px rgba(0,0,0,.45)}' +
    '.bio-btn.spot{background:var(--bio-ac);color:#fff;border-color:transparent;animation:bioPulse 2.4s ease-in-out infinite}' +
    '@keyframes bioPulse{50%{transform:scale(1.025)}}' +
    '.bio-h{margin:10px 0 -2px;font-size:13px;letter-spacing:.14em;text-transform:uppercase;font-weight:700;opacity:.8;text-align:center}' +
    '.bio-note{margin:0;text-align:center;font-size:14px;opacity:.85;white-space:pre-line;max-width:44ch}' +
    '.bio-gate{position:absolute;inset:0;z-index:10;display:flex;align-items:center;justify-content:center;padding:28px;background:rgba(10,6,9,.6);backdrop-filter:blur(18px)}' +
    '.bio-gate>div{max-width:320px;width:100%;text-align:center;color:#fff;display:flex;flex-direction:column;gap:12px;font-family:system-ui,sans-serif}' +
    '.bio-gate b{font-size:44px;line-height:1}.bio-gate p{margin:0;font-size:14px;opacity:.9}' +
    '.bio-gate .go{background:var(--bio-ac);color:#fff;border:0;border-radius:999px;padding:14px;font-weight:700;font-size:15px;cursor:pointer}' +
    '.bio-gate .no{background:none;border:0;color:#fff;opacity:.7;cursor:pointer;font-size:13px}' +
    '@media (prefers-reduced-motion:reduce){.bio-btn.spot{animation:none}.bio-btn:hover{transform:none}}';

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function loadFonts(t) {
    var fams = [t.font_title, t.font_body].filter(Boolean);
    var href = 'https://fonts.googleapis.com/css2?' + fams.map(function (f) {
      return 'family=' + f.replace(/ /g, '+') + ':wght@400;600;700';
    }).join('&') + '&display=swap';
    if (document.querySelector('link[data-bio-font="' + href + '"]')) return;
    var l = document.createElement('link');
    l.rel = 'stylesheet'; l.href = href; l.setAttribute('data-bio-font', href);
    document.head.appendChild(l);
  }
  function adultOk() { try { return localStorage.getItem('bio_18') === '1'; } catch (e) { return false; } }
  function setAdult() { try { localStorage.setItem('bio_18', '1'); } catch (e) {} }

  function gate(root, onYes) {
    var g = el('div', 'bio-gate'), box = el('div');
    box.appendChild(el('b', '', '18+'));
    box.appendChild(el('p', '', 'This contains content for adults only. Confirm you are 18 or older to continue.'));
    var yes = el('button', 'go', "I'm 18 or older"), no = el('button', 'no', 'Take me back');
    yes.type = no.type = 'button';
    yes.onclick = function () { setAdult(); g.remove(); if (onYes) onYes(); };
    no.onclick = function () { g.remove(); if (!onYes) history.length > 1 ? history.back() : (location.href = 'about:blank'); };
    box.appendChild(yes); box.appendChild(no); g.appendChild(box); root.appendChild(g);
  }

  window.renderBio = function (mount, cfg, opts) {
    opts = opts || {};
    var t = cfg.theme || {};
    if (!document.getElementById('bio-css')) {
      var st = el('style'); st.id = 'bio-css'; st.textContent = CSS; document.head.appendChild(st);
    }
    loadFonts(t);
    mount.innerHTML = '';
    var root = el('div', 'bio st-' + (t.btn_style || 'fill'));
    var r = { square: '0px', round: '16px', pill: '999px' }[t.radius] || '16px';
    root.style.cssText = '--bio-r:' + r + ';--bio-bb:' + t.btn_bg + ';--bio-bt:' + t.btn_text +
      ';--bio-ac:' + t.accent + ';--bio-tx:' + t.text + ';color:' + t.text +
      ';font-family:"' + t.font_body + '",system-ui,sans-serif;';
    if (t.bg_type === 'image' && t.bg_image_url) {
      root.style.backgroundImage = 'linear-gradient(rgba(0,0,0,.25),rgba(0,0,0,.25)),url("' + t.bg_image_url + '")';
      root.style.backgroundColor = t.bg1;
    } else if (t.bg_type === 'gradient') {
      root.style.background = 'linear-gradient(180deg,' + t.bg1 + ',' + t.bg2 + ')';
    } else {
      root.style.background = t.bg1;
    }
    var inner = el('div', 'bio-in');
    if (cfg.avatar_url) {
      var av = el('img', 'bio-av ' + (t.avatar_shape || 'circle'));
      av.src = cfg.avatar_url; av.alt = '';
      av.onerror = function () { av.remove(); };
      inner.appendChild(av);
    }
    var nm = el('h1', 'bio-name', cfg.name || '');
    nm.style.fontFamily = '"' + t.font_title + '",' + '"' + t.font_body + '",sans-serif';
    inner.appendChild(nm);
    if (cfg.bio) inner.appendChild(el('p', 'bio-text', cfg.bio));
    var soc = el('div', 'bio-soc');
    (cfg.socials || []).forEach(function (s) {
      if (!NETS[s.net] || !s.url) return;
      var a = el('a'); a.href = s.url; a.target = '_blank'; a.rel = 'noopener nofollow';
      a.setAttribute('aria-label', NETS[s.net].label);
      a.innerHTML = badge(s.net, t.icon_color === 'brand');
      soc.appendChild(a);
    });
    var socTop = t.socials_pos !== 'bottom';
    if (soc.children.length && socTop) inner.appendChild(soc);

    (cfg.blocks || []).forEach(function (b) {
      if (b.hidden && !opts.preview) return;
      if (b.type === 'header') { inner.appendChild(el('p', 'bio-h', b.title)); return; }
      if (b.type === 'text') { inner.appendChild(el('p', 'bio-note', b.text)); return; }
      var a = el('a', 'bio-btn' + (b.spotlight ? ' spot' : ''));
      if (b.hidden) a.style.opacity = '.4';
      a.href = b.href || '#';
      if (b.type === 'link') { a.target = '_blank'; a.rel = 'noopener nofollow'; }
      if (b.thumb_url) { var im = el('img'); im.src = b.thumb_url; im.alt = ''; a.appendChild(im); }
      else if (b.type === 'link' && netOf(b.url)) { var ic = el('span', 'bio-bic'); ic.innerHTML = icon(netOf(b.url)); a.appendChild(ic); }
      a.appendChild(el('span', '', b.title || (b.type === 'chat' ? 'Chat with me' : 'Link')));
      if (b.adult) a.appendChild(el('span', 'tag', '18+'));
      if (opts.preview) a.onclick = function (e) { e.preventDefault(); };
      else if (b.adult && cfg.gate === 'button') {
        a.onclick = function (e) {
          if (adultOk()) return;
          e.preventDefault();
          gate(root, function () { a.target === '_blank' ? window.open(a.href, '_blank', 'noopener') : (location.href = a.href); });
        };
      }
      inner.appendChild(a);
    });
    if (soc.children.length && !socTop) inner.appendChild(soc);
    root.appendChild(inner);
    mount.appendChild(root);
    if (!opts.preview && cfg.gate === 'page' && !adultOk()) gate(root);
  };
})();
