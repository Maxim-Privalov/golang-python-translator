package main

import "fmt"

func main() {
	var x = 10
	y := 20
	var faa []int

	fmt.Println(fa(x, y))
	fmt.Println(faa)
}

func fa(x, y int) int {
	if x < y {
		return x
	}
	return y
}
