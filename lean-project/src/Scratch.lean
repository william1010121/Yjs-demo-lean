
#eval 2+3
--right hand side?

#check 3+5

-- i can see!

-- 看得到 你的藍色滑鼠
-- 有一個分析面板
-- 上面寫 3+4：Nat 之類的
-- 應該#check 有起作用
-- how to get INFOR VIEW like VS

-- 要在 theorem 底下會比較有？
theorem test_info_view (P Q : Prop) (h1 : P) (h2 : P → Q) : Q := by
  apply h2
  exact h1
  




theorem test_info_view2 (P Q : Prop) (h1 : P) (h2 : P → Q) : Q := by
  apply h2
  apply h1
  

-- 看起來更新有點慢
-- 他好像只會顯示錯誤訊息 和打印訊息
-- info view 要根據使用者的滑鼠位置而改變顯示資訊
-- 好像不是伺服器端能處理的 每個客戶端看到的要不一樣
-- >我不清楚LEAN和VS各給多少編譯
-- 我後端沒擷取到LEAN COMPILER的完全資訊 我猜
